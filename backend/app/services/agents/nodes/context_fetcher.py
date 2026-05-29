"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. Embeds the question via Cohere, searches Neo4j for templates/tables/columns,
fetches business terms and intents, applies community scoping, budget trims,
and injects short/long-term memory context.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import asyncio
import hashlib
import json
import time

from app.core.logger import logger
from app.services.agents import neo4j_client, redis_client
from app.services.agents.memory import long_term, short_term
from app.services.agents.state import AnalyticsState

import re as _re

# Patterns that signal a follow-up response with no independent semantic content.
# The user is continuing a prior thread rather than asking a new question.
_FOLLOWUP_SIGNALS = _re.compile(
    r"^("
    # pure affirmatives
    r"yes|yep|yup|yeah|sure|ok|okay|alright|absolutely|of course|definitely|"
    r"sounds good|great|perfect|proceed|continue|go ahead|do it|please|"
    # continuation phrases
    r"show me|show that|more details?|more info|tell me more|elaborate|"
    r"break (it|that|this|them) down|drill (down|into)|"
    r"what about|and (also|then|what)|"
    # dangling pronouns — reference prior context with no standalone meaning
    r"(show|give|get|fetch|pull|display|list|what('s| is| are)) (me )?(the |those |that |it|them|more)"
    r")[\s.!?]*$",
    _re.IGNORECASE,
)

# If the question is this many words or fewer AND there is an active session
# summary, treat it as a contextual follow-up even if it doesn't match the
# patterns above (catches things like "by bank", "last month", "Q1 only").
_SHORT_FOLLOWUP_WORD_LIMIT = 5


def _is_followup_question(question: str, has_session_context: bool) -> bool:
    """Return True when the question cannot stand alone semantically.

    Three signals:
    1. Matches a known continuation pattern (affirmative, dangling pronoun, etc.)
    2. Very short question (≤ 5 words) with active session context
    3. Pure punctuation / single character — always a follow-up
    """
    stripped = question.strip()
    if not stripped or len(stripped) <= 2:
        return True
    if _FOLLOWUP_SIGNALS.match(stripped):
        return True
    if has_session_context:
        word_count = len(stripped.split())
        if word_count <= _SHORT_FOLLOWUP_WORD_LIMIT:
            return True
    return False


def _reconstruct_question(question: str, session_summary: str, previous_follow_ups: list[str]) -> str:
    """Build a semantically useful search query when the raw question is a follow-up.

    Priority order:
    1. First suggested follow-up from Q1 synthesis — most specific (e.g. "Break down by bank?")
    2. Last 300 chars of session summary — contains recent intent and entity identifiers
    3. Raw question as fallback (better than nothing)
    """
    if previous_follow_ups:
        return previous_follow_ups[0]
    if session_summary and len(session_summary) > 20:
        return session_summary[-300:].strip()
    return question


_STRIP_PROPS = {
    # Embeddings and hashes
    "cohere_embedding", "source_hash",
    # Graph analytics metrics
    "pagerank_score", "betweenness_score", "wcc_component_id",
    "leiden_gamma", "modularity_contribution",
    # Generation metadata
    "enrichment_status", "description_model", "embedding_model",
    "embedding_generated_at", "description_generated_at", "created_at", "updated_at",
    # Statistical internals
    "ordinal_position", "null_frac", "n_distinct", "same_name_col_count",
    # Redshift storage details
    "encoded_pct", "size_mb", "type_confidence", "distkey_col", "diststyle",
    "sortkey_type", "sortkey1",
    # Denormalized FTS text fields (indexed separately, not for LLM)
    "synonyms_text", "intent_tags_text", "top_values_text",
    # PII / storage flags
    "is_notnull", "is_nullable", "is_pii", "pii_type", "is_pk",
    # Table graph topology flags
    "is_isolated", "is_subquery_anchor", "is_weakly_bridged",
    "ontology_class", "schema", "table_type_db", "version",
    # Column — verbose count-suffixed frequency values (value_vocabulary is cleaner)
    "top_freq_values",
    # Community graph stats
    "dominant_domain_confidence", "domain_distribution", "run_date",
    # Column internal
    "temporal_grain",
    # Template internals
    "anchor_ontology_classes", "intent_scores", "source_line", "time_windowed",
}


async def context_fetcher(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("context_fetcher START | thread={} | question={}", state["thread_id"], state["question"][:80])

    try:
        # Load short-term memory first — needed for follow-up reconstruction.
        session_summary = short_term.get_session_summary(state["thread_id"])

        # Detect affirmative follow-ups ("yes", "sure", "go ahead") and reconstruct
        # the effective search query from session context before embedding.
        raw_question = state["question"]
        is_followup = _is_followup_question(raw_question, bool(session_summary))
        if is_followup:
            previous_follow_ups = state.get("follow_ups") or []
            search_query = _reconstruct_question(raw_question, session_summary or "", previous_follow_ups)
            logger.info(
                "context_fetcher | affirmative follow-up detected | reconstructed={}",
                search_query[:80],
            )
        else:
            search_query = raw_question

        embedding = await _get_embedding(search_query)

        # ── Phase 1: Template search (hybrid: vector + FTS) ───────────────────
        templates         = neo4j_client.search_query_templates(embedding)
        templates_fts     = neo4j_client.search_query_templates_fulltext(search_query)
        templates_merged  = _merge_template_results(templates, templates_fts)

        # Template anchor tables are surfaced via template_vector / template_fts paths;
        # no synthetic 0.95 score boost — avoid biasing table ranking toward templates.
        anchor_source: list[dict] = []

        # ── Phase 2: 7-path table discovery ───────────────────────────────────
        tables_direct_v      = neo4j_client.search_tables_vector(embedding)
        tables_direct_fts    = neo4j_client.search_tables_fulltext(search_query)
        tables_via_tmpl_v    = neo4j_client.search_tables_via_templates_vector(embedding)
        tables_via_tmpl_fts  = neo4j_client.search_tables_via_templates_fulltext(search_query)
        tables_via_intent    = neo4j_client.search_tables_via_intents(embedding)
        tables_via_comm      = neo4j_client.search_tables_via_community(embedding)
        tables_via_domain    = neo4j_client.search_tables_via_domain(embedding)

        logger.info("context_fetcher | path=direct_vector       | tables={}", [t.get("fqn") for t in tables_direct_v])
        logger.info("context_fetcher | path=direct_fts          | tables={}", [t.get("fqn") for t in tables_direct_fts])
        logger.info("context_fetcher | path=template_v→requires | tables={}", [(t.get("fqn"), t.get("matched_via")) for t in tables_via_tmpl_v])
        logger.info("context_fetcher | path=template_fts→req    | tables={}", [(t.get("fqn"), t.get("matched_via")) for t in tables_via_tmpl_fts])
        logger.info("context_fetcher | path=intent_traversal    | tables={}", [(t.get("fqn"), t.get("matched_via")) for t in tables_via_intent])
        logger.info("context_fetcher | path=community_traversal | tables={}", [(t.get("fqn"), t.get("matched_via")) for t in tables_via_comm])
        logger.info("context_fetcher | path=domain_traversal    | tables={}", [(t.get("fqn"), t.get("matched_via")) for t in tables_via_domain])
        logger.info("context_fetcher | path=template_anchor     | tables={}", [t.get("fqn") for t in anchor_source])

        # ── Phase 3: Merge + score tables from 8 paths (7 semantic + anchor) ─
        tables = _merge_table_sources({
            "direct_vector":    tables_direct_v,
            "direct_fts":       tables_direct_fts,
            "template_vector":  tables_via_tmpl_v,
            "template_fts":     tables_via_tmpl_fts,
            "intent":           tables_via_intent,
            "community":        tables_via_comm,
            "domain":           tables_via_domain,
            "template_anchor":  anchor_source,
        })
        logger.info("context_fetcher | merged_tables={} | path_counts={}",
                    [t.get("fqn") for t in tables],
                    {t.get("fqn"): t.get("retrieval_paths") for t in tables})

        # ── Phase 3.5: JoinPath expansion ─────────────────────────────────────
        semantic_fqns = {t["fqn"] for t in tables if t.get("fqn")}
        tables_via_joins = neo4j_client.search_tables_via_joinpaths(list(semantic_fqns))
        logger.info("context_fetcher | path=joinpath_expansion | new_tables={}",
                    [(t.get("fqn"), t.get("matched_via")) for t in tables_via_joins])
        existing_fqns = set(semantic_fqns)
        for t in tables_via_joins:
            if t.get("fqn") and t["fqn"] not in existing_fqns:
                t["retrieval_paths"] = ["joinpath"]
                tables.append(t)
                existing_fqns.add(t["fqn"])

        # ── Phase 4: Column loading — HAS_COLUMN (with enrichment) + hybrid rank
        candidate_fqns = {t["fqn"] for t in tables if t.get("fqn")}
        columns_graph  = neo4j_client.get_columns_for_tables(list(candidate_fqns))
        columns_v      = neo4j_client.search_columns_vector(embedding)
        columns_fts    = neo4j_client.search_columns_fulltext(search_query)
        table_priority = {t["fqn"]: len(t.get("retrieval_paths") or []) for t in tables}
        columns = _merge_column_sources(columns_graph, columns_v, columns_fts, candidate_fqns, table_priority)
        logger.info("context_fetcher | cols_graph={} | cols_vector={} | cols_fts={} | cols_merged={}",
                    len(columns_graph), len(columns_v), len(columns_fts), len(columns))

        # ── Phase 5: Business terms (hybrid: vector + FTS) + intents ──────────
        business_terms_v   = neo4j_client.search_business_terms_vector(embedding)
        business_terms_fts = neo4j_client.search_business_terms_fulltext(search_query)
        business_terms     = _merge_business_terms(business_terms_v, business_terms_fts)
        intents            = neo4j_client.search_intents(embedding)
        logger.info("context_fetcher | business_terms={} | intents={}",
                    [b.get("term") for b in business_terms], [i.get("name") for i in intents])

        templates = _trim_objects(templates_merged)
        tables    = _trim_objects(tables)
        columns   = _trim_objects(columns)

        memory_context = await long_term.retrieve_user_memory(state["user_id"], search_query)

        semantic_context = {
            "templates": templates,
            "tables": tables,
            "columns": columns,
            "business_terms": business_terms,
            "intents": intents,
            "session_summary": session_summary,
            "memory_context": memory_context,
            # effective_question lets downstream nodes know what was actually searched
            "effective_question": search_query if is_followup else None,
            "is_followup": is_followup,
        }

        await _enrich_columns_from_redshift(semantic_context, str(state["thread_id"]))

        matched_templates = semantic_context.get("templates") or []
        tables_found = [t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")]
        columns_found = len(semantic_context.get("columns") or [])

        if not matched_templates:
            logger.warning(
                "context_fetcher | NO matched templates | thread={} | question={}",
                state["thread_id"], state["question"][:80],
            )
        if not tables_found:
            logger.warning(
                "context_fetcher | NO tables in context | thread={} | question={}",
                state["thread_id"], state["question"][:80],
            )

        anchor_fqns_from_top_template = (
            matched_templates[0].get("anchor_table_fqns") or []
            if matched_templates else []
        )
        missing_anchors = [f for f in anchor_fqns_from_top_template if f not in tables_found]
        if missing_anchors:
            logger.warning(
                "context_fetcher | template anchor tables not in merged tables | missing={} | thread={}",
                missing_anchors, state["thread_id"],
            )

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | templates={} | tables={} | cols={} | top_template={} | joins={}",
            state["thread_id"],
            is_followup,
            len(matched_templates),
            tables_found,
            columns_found,
            matched_templates[0].get("id") if matched_templates else "none",
            len(semantic_context.get("available_joins") or []),
        )
        return {"semantic_context": semantic_context, "error": None}

    except Exception as e:
        logger.error("context_fetcher FAILED | thread={} | error={}", state["thread_id"], e)
        return {"error": "semantic_layer_unavailable", "semantic_context": None}


async def _get_embedding(text: str) -> list[float]:
    normalized = text.strip().lower()
    cached = redis_client.get_embedding(normalized)

    if cached:
        logger.debug("cohere embed | cache_hit=True | ms=0")
        return cached

    t0 = time.monotonic()
    from app.services.embeddings import embed_question
    embedding = await embed_question(normalized)
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.debug("cohere embed | cache_hit=False | ms={:.0f}", elapsed_ms)

    redis_client.set_embedding(normalized, embedding)
    return embedding


def _tokenize_with_bigrams(text: str) -> list[str]:
    """Generate unigrams and bigrams, plus underscore-joined bigrams for term matching."""
    import re
    words = re.findall(r"\b\w+\b", text.lower())
    tokens = list(words)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        tokens.append(bigram)
        tokens.append(f"{words[i]}_{words[i+1]}")
    return tokens


def _apply_community_scoping(tables: list[dict]) -> list[dict]:
    """Filter tables to those in the same communities as fulltext matches."""
    if not tables:
        return tables
    community_ids = {t.get("community_id") for t in tables if t.get("community_id")}
    if not community_ids:
        return tables
    scoped = [t for t in tables if t.get("community_id") in community_ids]
    return scoped if scoped else tables


def _trim_objects(objects: list[dict]) -> list[dict]:
    trimmed = []
    for obj in objects:
        cleaned = {k: v for k, v in obj.items() if k not in _STRIP_PROPS}
        if "description" in cleaned and isinstance(cleaned["description"], str):
            cleaned["description"] = cleaned["description"][:120]
        for list_field, limit in [
            ("synonyms", 3), ("sample_values", 5),
            ("value_vocabulary", 5), ("value_aliases", 5),
            ("variants", 5), ("natural_dimensions", 6), ("natural_measures", 6),
        ]:
            if list_field in cleaned and isinstance(cleaned[list_field], list):
                cleaned[list_field] = cleaned[list_field][:limit]
        trimmed.append(cleaned)
    return trimmed


def _merge_template_results(vector_results: list[dict], fts_results: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for t in vector_results:
        tid = t.get("id")
        if tid:
            seen[tid] = dict(t)
    for t in fts_results:
        tid = t.get("id")
        if not tid:
            continue
        if tid not in seen:
            seen[tid] = dict(t)
        else:
            seen[tid]["score"] = max(seen[tid].get("score") or 0.0, t.get("score") or 0.0) + 0.05
    return sorted(seen.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:5]


def _merge_table_sources(sources: dict[str, list[dict]]) -> list[dict]:
    seen: dict[str, dict] = {}
    for path_name, table_list in sources.items():
        for t in table_list:
            fqn = t.get("fqn")
            if not fqn:
                continue
            if fqn not in seen:
                seen[fqn] = dict(t)
                seen[fqn]["retrieval_paths"] = [path_name]
            else:
                seen[fqn]["retrieval_paths"].append(path_name)
                cur_score = seen[fqn].get("score") or 0.0
                new_score = t.get("score") or 0.0
                seen[fqn]["score"] = max(cur_score, new_score) + 0.05
    merged = sorted(
        seen.values(),
        key=lambda x: (len(x.get("retrieval_paths") or []), x.get("score") or 0.0),
        reverse=True,
    )
    return merged[:10]


def _merge_column_sources(
    graph_cols: list[dict],
    vector_cols: list[dict],
    fts_cols: list[dict],
    candidate_fqns: set[str],
    table_priority: dict[str, int],
) -> list[dict]:
    relevant_keys: dict[tuple, float] = {}
    for c in vector_cols:
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] and key[1] and key[0] in candidate_fqns:
            relevant_keys[key] = max(relevant_keys.get(key, 0.0), c.get("score") or 0.0)
    for c in fts_cols:
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] and key[1] and key[0] in candidate_fqns:
            relevant_keys[key] = max(relevant_keys.get(key, 0.0), c.get("score") or 0.0) + 0.05

    graph_by_key: dict[tuple, dict] = {}
    for c in graph_cols:
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] and key[1]:
            graph_by_key[key] = c

    result: list[dict] = []
    seen: set[tuple] = set()
    for key, _ in sorted(relevant_keys.items(), key=lambda x: x[1], reverse=True):
        col = graph_by_key.get(key)
        if col is not None:
            result.append(col)
            seen.add(key)

    remaining = [
        c for c in graph_cols
        if (c.get("table_fqn"), c.get("name")) not in seen
        and c.get("table_fqn") and c.get("name")
    ]
    remaining.sort(key=lambda c: table_priority.get(c.get("table_fqn", ""), 0), reverse=True)
    result.extend(remaining)
    return result[:40]


def _merge_business_terms(vector_results: list[dict], fts_results: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for bt in vector_results:
        term = bt.get("term")
        if term:
            seen[term] = dict(bt)
    for bt in fts_results:
        term = bt.get("term")
        if not term:
            continue
        if term not in seen:
            seen[term] = dict(bt)
        else:
            seen[term]["score"] = max(seen[term].get("score") or 0.0, bt.get("score") or 0.0) + 0.05
    return sorted(seen.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:5]


# ── Column enrichment from Redshift ───────────────────────────────────────────

_NON_CATEGORICAL = (
    "int", "float", "double", "decimal", "numeric", "real",
    "date", "timestamp", "bool", "bytea", "json", "jsonb",
)
_CATEGORICAL = ("char", "varchar", "text", "bpchar", "nchar", "nvarchar")


def _should_probe(col_name: str, data_type: str) -> bool:
    dtype = data_type.lower()
    col = col_name.lower()
    if "uuid" in dtype or "uuid" in col:
        return False
    if col == "id" or col.endswith("_id") or col.endswith("_ref"):
        return False
    if any(t in dtype for t in _NON_CATEGORICAL):
        return False
    return any(t in dtype for t in _CATEGORICAL)


def _get_probe_candidates(semantic_context: dict) -> dict[str, set[str]]:
    """Return {table_fqn: set(col_names)} for join key columns + low-selectivity Neo4j columns."""
    table_fqns = [t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")]
    join_cols: dict[str, set[str]] = {fqn: set() for fqn in table_fqns}

    try:
        joins = neo4j_client.get_direct_joins(table_fqns)
        for j in joins:
            from_fqn = j.get("from_fqn", "")
            to_fqn = j.get("to_fqn", "")
            if j.get("from_col") and from_fqn in join_cols:
                join_cols[from_fqn].add(j["from_col"])
            if j.get("to_col") and to_fqn in join_cols:
                join_cols[to_fqn].add(j["to_col"])
    except Exception:
        pass

    for col in (semantic_context.get("columns") or []):
        if col.get("filter_selectivity") == "low":
            fqn = col.get("table_fqn", "")
            if fqn in join_cols:
                join_cols[fqn].add(col["name"])

    return join_cols


async def _enrich_columns_from_redshift(semantic_context: dict, thread_id: str) -> None:
    """Enrich semantic_context columns in-place with Redshift-probed distinct values.

    For each table in context:
    1. Discover join key columns missing from Neo4j via information_schema.columns.
    2. Probe DISTINCT values for all probeable VARCHAR columns (join keys + low-selectivity).

    Writes filter_values (full 100), sample_values (first 5), filter_selectivity into
    column dicts in-memory — never touches Neo4j.
    """
    from app.services.agents.redshift_client import execute_query, fetch_table_schema

    probe_candidates = _get_probe_candidates(semantic_context)
    if not probe_candidates:
        return

    # Collect direct joins for FK candidate discovery after Step A
    table_fqns = list(probe_candidates.keys())
    direct_joins: list[dict] = []
    try:
        direct_joins = neo4j_client.get_direct_joins(table_fqns)
    except Exception:
        pass

    # Build an index of existing columns for fast lookup and mutation
    col_index: dict[tuple[str, str], dict] = {}
    for col in (semantic_context.get("columns") or []):
        fqn = col.get("table_fqn", "")
        name = col.get("name", "")
        if fqn and name:
            col_index[(fqn, name)] = col

    # Step A — fetch the FULL Redshift schema for each table (via fetch_table_schema which
    # writes to Redis and never overwrites with a partial match-subset).
    # Returns all real columns — caller builds confirmed_in_redshift from this.
    async def _validate_table(fqn: str, col_names: set) -> tuple[str, list]:
        parts = fqn.split(".")
        if len(parts) != 2:
            return fqn, []
        schema_name, table_name = parts[0], parts[1]
        all_cols = await fetch_table_schema(schema_name, table_name)
        return fqn, all_cols

    # Cap concurrent schema fetches — pool has 6 connections; leave 2 for actual
    # query execution. fetch_table_schema hits Redis first so only cache misses
    # consume pool connections, but on first run all tables can miss simultaneously.
    sem = asyncio.Semaphore(3)

    async def _validate_table_guarded(fqn: str, col_names: set) -> tuple[str, list]:
        async with sem:
            return await _validate_table(fqn, col_names)

    step_a_tasks = [
        _validate_table_guarded(fqn, col_names)
        for fqn, col_names in probe_candidates.items()
        if col_names
    ]
    step_a_results = await asyncio.gather(*step_a_tasks, return_exceptions=True)

    # fqn → [[col_name, data_type], ...] for all real columns — used by FK discovery below
    step_a_all_cols_map: dict[str, list[list]] = {}

    for result in step_a_results:
        if isinstance(result, BaseException):
            logger.warning("context_fetcher | step_a validation error | {}", result)
            continue
        fqn, rows = result
        step_a_all_cols_map[fqn] = rows
        col_names = probe_candidates.get(fqn, set())

        confirmed_in_redshift: set[str] = {row[0] for row in rows}
        probe_candidates[fqn] = col_names & confirmed_in_redshift

        # Remove stale Neo4j columns for this table — keep only what Redshift confirms.
        # All downstream agents only see valid column names.
        before = len(semantic_context.get("columns") or [])
        semantic_context["columns"] = [
            c for c in (semantic_context.get("columns") or [])
            if not (c.get("table_fqn") == fqn and c.get("name") not in confirmed_in_redshift)
        ]
        dropped = before - len(semantic_context.get("columns") or [])
        if dropped:
            logger.info("context_fetcher | stale_cols_removed | fqn={} | dropped={}", fqn, dropped)

        # Add ALL real Redshift columns missing from Neo4j catalog to semantic_context.
        # This includes potential FK columns (e.g., bank_account.bank_code) that Neo4j
        # may have stored under a wrong name or never catalogued.
        for row in rows:
            col_name, data_type = row[0], row[1]
            key = (fqn, col_name)
            if key not in col_index:
                new_col = {
                    "table_fqn": fqn,
                    "name": col_name,
                    "data_type": data_type,
                    "sample_values": [],
                    "filter_values": [],
                }
                semantic_context.setdefault("columns", []).append(new_col)
                col_index[key] = new_col
                logger.debug(
                    "context_fetcher | discovered col | {}.{} | dtype={}",
                    fqn, col_name, data_type,
                )

    # FK candidate discovery: for each JOINS_TO edge where the recorded join column was
    # stripped (not in Redshift), find name-pattern candidates from the full schema.
    # These get added to probe_candidates so Step B probes their distinct values.
    # After Step B, overlap confirmation tells us which candidate is the real FK.
    fk_discoveries: list[tuple[str, str, str, str]] = []  # (from_fqn, cand_col, to_fqn, to_col)

    for j in direct_joins:
        from_fqn = j.get("from_fqn", "")
        to_fqn = j.get("to_fqn", "")
        from_col = j.get("from_col", "")
        to_col = j.get("to_col", "")

        if not from_fqn or not from_col or not to_fqn:
            continue

        # Skip if from_col is still valid (not stripped)
        if from_col in probe_candidates.get(from_fqn, set()):
            continue

        all_cols_for_from = step_a_all_cols_map.get(from_fqn, [])
        if not all_cols_for_from:
            continue

        to_table = to_fqn.rsplit(".", 1)[-1]
        already_probed = probe_candidates.get(from_fqn, set())
        col_names_available = {r[0] for r in all_cols_for_from}

        # Pattern candidates: starts with {to_table}_ or ends with _{to_table}
        candidates = [
            c for c in col_names_available
            if (c.startswith(to_table + "_") or c.endswith("_" + to_table))
            and c not in already_probed
        ]

        if candidates:
            logger.info(
                "context_fetcher | fk_candidates | from={} | stripped_col={} | to={} | candidates={}",
                from_fqn, from_col, to_fqn, candidates,
            )

        for candidate in candidates:
            probe_candidates.setdefault(from_fqn, set()).add(candidate)
            key = (from_fqn, candidate)
            if key not in col_index:
                dtype = next(
                    (r[1] for r in all_cols_for_from if r[0] == candidate),
                    "character varying",
                )
                new_col = {
                    "table_fqn": from_fqn,
                    "name": candidate,
                    "data_type": dtype,
                    "sample_values": [],
                    "filter_values": [],
                }
                semantic_context.setdefault("columns", []).append(new_col)
                col_index[key] = new_col
            if to_col:
                fk_discoveries.append((from_fqn, candidate, to_fqn, to_col))

    # Step B — probe DISTINCT values for all probeable columns
    probe_stats = {"hits": 0, "misses": 0, "errors": 0}

    async def _probe_col(fqn: str, col_name: str, col_dict: dict) -> None:
        data_type = col_dict.get("data_type", "")
        if not _should_probe(col_name, data_type):
            return

        cached = redis_client.get_filter_values(fqn, col_name)
        if cached is not None:
            col_dict["filter_values"] = cached
            col_dict["sample_values"] = cached[:5]
            col_dict["filter_selectivity"] = "low" if len(cached) <= 20 else "medium"
            probe_stats["hits"] += 1
            logger.info(
                "context_fetcher | probe cache_hit | {}.{} | count={}",
                fqn, col_name, len(cached),
            )
            return

        probe_stats["misses"] += 1
        # Scan a bounded sample then group — avoids full-table sort.
        # 5 000 rows is enough to surface representative distinct values on any
        # table; 50 000 caused 60 s timeouts on wide fact tables (lpp.gl_balance).
        # Probe timeout is intentionally short: a slow probe is skipped gracefully,
        # not allowed to block the pipeline for a minute.
        probe_sql = (
            f'SELECT val FROM ('
            f'SELECT CAST("{col_name}" AS VARCHAR) AS val '
            f'FROM {fqn} '
            f'WHERE "{col_name}" IS NOT NULL '
            f'LIMIT 5000'
            f') t GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 100'
        )
        try:
            _, rows = await execute_query(probe_sql, timeout_s=60, thread_id=thread_id)
            values = [str(r[0]) for r in rows if r and r[0] is not None]
            redis_client.set_filter_values(fqn, col_name, values, ttl=86400)
            col_dict["filter_values"] = values
            col_dict["sample_values"] = values[:5]
            col_dict["filter_selectivity"] = "low" if len(values) <= 20 else "medium"
            logger.info(
                "context_fetcher | probe redshift | {}.{} | count={}",
                fqn, col_name, len(values),
            )
        except Exception as e:
            probe_stats["errors"] += 1
            probe_stats["misses"] -= 1
            logger.warning(
                "context_fetcher | probe failed | {}.{} | error={}", fqn, col_name, e
            )

    async def _probe_guarded(fqn: str, col_name: str, col_dict: dict) -> None:
        async with sem:
            await _probe_col(fqn, col_name, col_dict)

    tasks = []
    for fqn, col_names in probe_candidates.items():
        for col_name in col_names:
            col_dict = col_index.get((fqn, col_name))
            if col_dict is not None:
                tasks.append(_probe_guarded(fqn, col_name, col_dict))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        total = probe_stats["hits"] + probe_stats["misses"] + probe_stats["errors"]
        logger.info(
            "context_fetcher | probe summary | total={} | cache_hits={} | redshift={} | errors={}",
            total, probe_stats["hits"], probe_stats["misses"], probe_stats["errors"],
        )

    # FK overlap confirmation: for each FK candidate column probed above, check whether
    # its distinct values overlap with the referenced table's join column values.
    # Overlap confirms the FK relationship — store as suggested_join on the table entry
    # so sql_generator can include it in UNRESOLVED JOIN PAIRS.
    for from_fqn, candidate_col, to_fqn, to_col in fk_discoveries:
        from_vals = set(redis_client.get_filter_values(from_fqn, candidate_col) or [])
        to_vals = set(redis_client.get_filter_values(to_fqn, to_col) or [])
        if not (from_vals and to_vals):
            continue
        overlap = from_vals & to_vals
        if overlap:
            suggested = f"{from_fqn}.{candidate_col} = {to_fqn}.{to_col}"
            for t in (semantic_context.get("tables") or []):
                if t.get("fqn") == from_fqn:
                    t.setdefault("suggested_joins", []).append(suggested)
                    break
            logger.info(
                "context_fetcher | fk_confirmed | {}.{} → {}.{} | overlap={}",
                from_fqn, candidate_col, to_fqn, to_col, len(overlap),
            )

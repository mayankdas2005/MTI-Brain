"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. Embeds the question via Cohere, searches Neo4j for templates/tables/columns,
fetches business terms and intents, applies community scoping, budget trims,
and injects short/long-term memory context.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import hashlib
import json
import time

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.memory import long_term, short_term
from app.services.neo4j_analytics.state import AnalyticsState

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

        # Build a virtual "template_anchor" source from the top-matched template's
        # anchor_table_fqns. These are pre-validated primary tables for this query
        # type — giving them one extra retrieval path breaks score ties vs. semantically
        # similar but wrong tables (e.g. forecast_cash_flow vs forecast_vs_actual).
        anchor_source: list[dict] = []
        if templates_merged:
            for fqn in (templates_merged[0].get("anchor_table_fqns") or []):
                anchor_source.append({"fqn": fqn, "score": 0.95})

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

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | templates={} | tables={} | cols={}",
            state["thread_id"], is_followup, len(templates), len(tables), len(columns),
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

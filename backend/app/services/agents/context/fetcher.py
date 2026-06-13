"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. No Redshift. Embeds the question, runs 8-path table discovery,
detects cross-domain queries, loads columns with join-critical prioritization,
and assembles SemanticContext for downstream agents.

Post-discovery work is split into two parallel groups:
  Group A — independent of tables: templates, business terms, intents, memory
  Group B — depends on tables: cross-domain, join-critical cols, column loading

Both groups run concurrently via asyncio.gather().  All sync Neo4j calls inside
Group B are wrapped in asyncio.to_thread() so they never block the event loop.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.core.retry import retry_async, retry_sync
from app.services.agents import neo4j_client
from app.services.agents.memory import long_term, short_term
from app.services.agents.state import AnalyticsState
from . import helpers, table_discovery, column_loader, cross_domain


_CANONICAL_DOMAINS_CTX = {
    "cash_and_liquidity": ["cash", "liquidity", "balance", "sweep", "intercompany"],
    "benchmarking": ["benchmark", "sofr", "sonia", "rate index", "interest rate"],
    "debt_and_capital": ["debt", "credit", "facility", "borrowing", "capital"],
    "fx_and_hedging": ["fx", "foreign exchange", "hedge", "forward", "derivative"],
    "forecasting": ["forecast", "projection", "variance"],
    "fraud": ["fraud", "risk score", "chargeback"],
    "erp_reconciliation": ["reconciliation", "gl", "general ledger", "close"],
    "investments": ["investment", "portfolio", "deposit", "bond"],
    "reference": ["currency", "counterparty", "master data"],
    "knowledge_graph": ["institutional", "tribal", "sme"],
}


def _normalize_domain_name(raw: str) -> str:
    raw_lower = raw.lower().strip()
    if raw_lower in _CANONICAL_DOMAINS_CTX:
        return raw_lower
    for canonical, keywords in _CANONICAL_DOMAINS_CTX.items():
        if any(kw in raw_lower for kw in keywords):
            return canonical
    return raw_lower


async def context_fetcher(state: AnalyticsState, config: RunnableConfig) -> dict:
    entity_tokens = state.get("entity_tokens") or None
    # search_variants are corrected/expanded entity tokens from intake_classifier (abbrev expansions,
    # typo fixes). They replace raw entity_tokens everywhere in discovery + downstream prompts.
    search_variants = state.get("search_variants") or entity_tokens or None
    search_terms    = state.get("search_terms") or []
    logger.info(
        "context_fetcher START | thread={} | question={} | entity_tokens={} | search_variants={} | search_terms={}",
        state["thread_id"], state["question"][:80], entity_tokens, search_variants, search_terms,
    )

    try:
        # ── Short-term memory + follow-up detection ────────────────────────────
        session_summary = short_term.get_session_summary(state["thread_id"]) or state.get("summary") or ""
        raw_question    = state["question"]
        is_followup     = helpers.is_followup_question(raw_question, bool(session_summary))

        if is_followup:
            previous_follow_ups = state.get("follow_ups") or []
            search_query = helpers.reconstruct_question(
                raw_question, session_summary or "", previous_follow_ups
            )
            effective_question = search_query
            logger.info("context_fetcher | follow-up reconstructed | search={}", search_query[:80])
        elif state.get("is_refinement") and state.get("prior_question"):
            prior_q = (state["prior_question"] or "").strip()
            search_query = f"{prior_q} {raw_question}"
            effective_question = f"{prior_q}\n\nUser refinement: {raw_question.strip()}"
            logger.info("context_fetcher | refinement search | combined_query={}", search_query[:120])
        else:
            search_query = raw_question
            effective_question = raw_question

        # ── Embed question + focused search_terms in one Cohere batch call ────
        # all_to_embed[0] = full question (unchanged path for existing discovery logic)
        # all_to_embed[1:] = focused 2-4 word phrases from intake_classifier (Layer 2)
        all_to_embed = [search_query] + list(search_terms)
        all_embeddings = await retry_async(
            lambda: helpers.get_embeddings_batch(all_to_embed), service="redis"
        )
        embedding           = all_embeddings[0] if all_embeddings else []
        search_term_embeds  = all_embeddings[1:] if len(all_embeddings) > 1 else []
        logger.debug(
            "context_fetcher | batch_embed | total_texts={} | term_embeds={}",
            len(all_to_embed), len(search_term_embeds),
        )

        tokens    = helpers.tokenize_with_bigrams(search_query)
        domain_detected = helpers.domain_keyword_detected(search_query)

        # ── Table discovery: all paths in parallel ────────────────────────────
        # Returns (tables, pinned_fqns, bt_pin_data, intent_table_fqns, domain_table_fqns, entity_pinned_fqns)
        # Pass search_variants as entity_tokens so corrected/expanded tokens reach all discovery paths.
        tables, pinned_fqns, _bt_pin_data, intent_table_fqns, domain_table_fqns, entity_pinned_fqns = \
            await table_discovery.run_8_path_discovery(
                embedding, tokens, search_query, domain_detected,
                entity_tokens=search_variants,
                search_term_embeds=search_term_embeds,
                search_terms=list(search_terms),
            )

        if not tables:
            logger.warning("context_fetcher | NO tables found | thread={}", state["thread_id"])
            return {"error": "semantic_layer_unavailable", "semantic_context": None}

        # K2: canonical domain direct lookup for multi-domain queries (Q3 pattern)
        # Extracts DOMAIN lines from query_intent and runs a targeted BELONGS_TO Cypher query.
        # Guarantees domain coverage even when vector search misses a domain's primary tables.
        _canonical_domains = [
            _normalize_domain_name(l.split(":", 1)[1].strip())
            for l in (state.get("query_intent") or [])
            if l.startswith("DOMAIN:")
        ]
        if _canonical_domains:
            try:
                _domain_rows = await asyncio.to_thread(
                    neo4j_client.get_tables_for_canonical_domains, _canonical_domains
                )
                _domain_fqns = [r["fqn"] for r in _domain_rows if r.get("fqn")]
                if _domain_fqns:
                    _existing_fqns = {f for f in domain_table_fqns}
                    _new_fqns = [f for f in _domain_fqns if f not in _existing_fqns]
                    domain_table_fqns = list(domain_table_fqns) + _new_fqns
                    logger.info(
                        "context_fetcher | canonical_domain_lookup | domains={} | new_fqns={} | thread={}",
                        _canonical_domains, _new_fqns, state["thread_id"],
                    )
            except Exception as _e:
                logger.warning("context_fetcher | canonical_domain_lookup failed | error={}", _e)

        # ── W2: Universal query_intent line-type routing ────────────────────────
        # Scans typed lines from intake_classifier for additional retrieval signals.
        # CONDITION (limit words) / CONTEXT (enterprise words) → tribal policy lookup
        # DOMAIN ≥ 3 → cross-domain bridge table lookup
        _qi_lines = state.get("query_intent") or []
        _should_tribal = False
        _limit_words = frozenset({"below", "above", "minimum", "maximum", "threshold",
                                  "limit", "cap", "floor", "ceiling"})
        _enterprise_words = frozenset({"enterprise", "policy", "commitment", "board",
                                       "cfo", "obligation", "prior"})
        _tribal_kws: list[str] = []

        for _ln in _qi_lines:
            if _ln.startswith("CONDITION:"):
                _c = _concept_before_operator(_ln)
                if _c:
                    _tribal_kws.append(_c + " policy threshold")
                if any(w in _ln.lower() for w in _limit_words):
                    _should_tribal = True
            elif _ln.startswith("SCENARIO:"):
                _c = _first_noun_phrase(_ln)
                if _c:
                    _tribal_kws.append(_c + " scenario")
            elif _ln.startswith("COMPARISON:"):
                _c = _concept_before_vs(_ln)
                if _c:
                    _tribal_kws.append(_c + " baseline benchmark")
            elif _ln.startswith("CONTEXT:"):
                if any(w in _ln.lower() for w in _enterprise_words):
                    _should_tribal = True
                    _tribal_kws.extend(["policy limit", "enterprise context"])

        _has_multi_domain = state.get("has_multi_domain", False)

        async def _tribal_lookup() -> list[dict]:
            if not _should_tribal:
                return []
            from app.services.agents.nodes.tribal_retrieval import _run_cypher as _tribal_cypher
            kw1 = _tribal_kws[0] if _tribal_kws else "limit"
            kw2 = _tribal_kws[1] if len(_tribal_kws) > 1 else "policy"
            try:
                result = await asyncio.to_thread(_tribal_cypher, kw1, kw2)
                logger.info(
                    "context_fetcher | tribal_retrieval | kw1={} kw2={} | found={} | thread={}",
                    kw1, kw2, len(result), state["thread_id"],
                )
                return result
            except Exception as _te:
                logger.warning("context_fetcher | tribal_retrieval failed | error={}", _te)
                return []

        async def _bridge_lookup() -> list[str]:
            if not _has_multi_domain or not _canonical_domains:
                return []
            try:
                _brows = await asyncio.to_thread(
                    neo4j_client.get_cross_domain_bridges, _canonical_domains
                )
                _bfqns = [r["fqn"] for r in _brows if r.get("fqn")]
                if _bfqns:
                    logger.info(
                        "context_fetcher | bridge_lookup | domains={} | bridges={} | thread={}",
                        _canonical_domains, _bfqns, state["thread_id"],
                    )
                return _bfqns
            except Exception as _be:
                logger.warning("context_fetcher | bridge_lookup failed | error={}", _be)
                return []

        # ── Groups A + B + tribal + bridges all in parallel ─────────────────────
        (group_a, group_b, _policy_facts, _bridge_fqns) = await asyncio.gather(
            _fetch_group_a(embedding, search_query, state["user_id"]),
            _fetch_group_b(tables, embedding, search_query, entity_tokens=search_variants),
            _tribal_lookup(),
            _bridge_lookup(),
        )

        templates_merged, business_terms, intents, memory_context = group_a
        tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints, entity_col_tables = group_b

        # ── Consensus tables: found by 4+ independent discovery paths ─────────
        # Computed before trim_objects which may strip retrieval_paths metadata.
        # Used by anchor_resolver Signal 4 as a fallback when intent/business-term
        # signals don't fire (e.g. Neo4j data gaps). A table in 4+ paths has strong
        # multi-signal agreement; a false positive from entity_value alone has 1 path.
        consensus_table_fqns = [
            t["fqn"] for t in tables
            if t.get("fqn") and len(t.get("retrieval_paths") or []) >= 4
        ]

        # ── Trim for LLM display (join-critical gets full descriptions) ────────
        templates_trimmed = helpers.trim_objects(templates_merged)
        tables_trimmed    = helpers.trim_objects(tables)
        columns_trimmed   = helpers.trim_objects(display_columns, join_critical_cols=join_crit_cols)

        # ── Assemble SemanticContext ───────────────────────────────────────────
        # Z1: table_type bias for temporal anchor selection in anchor_resolver
        _table_types = {
            t["fqn"]: (t.get("table_type") or t.get("typical_join_role") or "fact")
            for t in tables_trimmed if t.get("fqn")
        }

        semantic_context = {
            "templates":             templates_trimmed,
            "tables":                tables_trimmed,
            "columns":               columns_trimmed,
            "_column_lookup":        col_lookup,
            "join_critical_cols":    list(join_crit_cols),
            "business_terms":        business_terms,
            "intents":               intents,
            "is_cross_domain":       is_cross_domain,
            "cross_domain_hub":      hub_info,
            "session_summary":       session_summary,
            "memory_context":        memory_context,
            "effective_question":    effective_question,
            "is_followup":           is_followup,
            "entity_hints":          entity_hints,
            "intent_table_fqns":     intent_table_fqns,
            "domain_table_fqns":     domain_table_fqns,
            "consensus_table_fqns":  consensus_table_fqns,
            "entity_pinned_fqns":    set(entity_pinned_fqns),
            "entity_col_tables":     list(entity_col_tables),
            # W2: policy/limit facts from CONDITION/CONTEXT line triggered tribal lookup
            "policy_facts":          _policy_facts,
            "policy_table_fqns":     [r.get("source_table_fqn") for r in _policy_facts if r.get("source_table_fqn")],
            # W2: bridge tables connecting 2+ domains (Signal 8 for anchor_resolver)
            "bridge_table_fqns":     _bridge_fqns,
            # Z1: table_type from Neo4j for temporal anchor bias (fact > dimension/reference/bridge)
            "table_types":           _table_types,
        }

        tables_found  = [t["fqn"] for t in tables_trimmed if t.get("fqn")]
        columns_found = len(columns_trimmed)

        if not tables_found:
            logger.warning("context_fetcher | NO tables in context | thread={}", state["thread_id"])

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | tables={} | cols={} | "
            "is_cross_domain={} | hub={} | entity_pinned={} | entity_col_tables={}",
            state["thread_id"], is_followup, tables_found, columns_found,
            is_cross_domain, hub_info.get("hub_table_fqn") if hub_info else "none",
            sorted(entity_pinned_fqns), sorted(entity_col_tables),
        )

        # ── T3/T5: Context summary for UI transparency panel ──────────────────
        _memory_items: list[str] = []
        if isinstance(memory_context, dict):
            for _m in (memory_context.get("memories") or []):
                _t = (_m.get("content") or _m.get("text") or "")[:120]
                if _t:
                    _memory_items.append(_t)
        elif isinstance(memory_context, list):
            for _m in memory_context:
                _t = (_m.get("content") or _m.get("text") or "")[:120]
                if _t:
                    _memory_items.append(_t)

        _trigger_line: str | None = next(
            (ln for ln in _qi_lines if ln.startswith(("CONDITION:", "CONTEXT:")) and _should_tribal),
            None,
        )

        context_summary = {
            "constraint_facts": [
                {
                    "table": r.get("source_table_fqn", ""),
                    "text": (r.get("fact") or r.get("text") or r.get("content") or "")[:160],
                }
                for r in _policy_facts if r.get("source_table_fqn")
            ],
            "constraint_trigger_line": _trigger_line,
            "memory_items": _memory_items[:5],
            "is_refinement": bool(state.get("is_refinement", False)),
            "is_followup": is_followup,
            "prior_question_preview": (state.get("prior_question") or "")[:100] if state.get("is_refinement") else None,
            "business_terms": [b.get("term") for b in business_terms if b.get("term")][:6],
            "decision_type": state.get("decision_type") or "lookup",
        }

        return {
            "semantic_context": semantic_context,
            "effective_question": effective_question,
            "context_summary": context_summary,
            "error": None,
        }

    except Exception as e:
        logger.error("context_fetcher FAILED | thread={} | error={}", state["thread_id"], e)
        return {"error": "semantic_layer_unavailable", "semantic_context": None}


async def _fetch_group_a(
    embedding: list[float],
    search_query: str,
    user_id: str,
) -> tuple:
    """Group A: all calls independent of table discovery results.

    Runs concurrently with Group B.  All Neo4j calls are wrapped in
    asyncio.to_thread() so they don't block the event loop.
    """
    async def _templates() -> tuple:
        v   = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_query_templates(embedding), service="neo4j"
        )
        fts = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_query_templates_fulltext(search_query), service="neo4j"
        )
        return table_discovery.merge_template_results(v, fts)

    async def _business_terms() -> list:
        v   = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_business_terms_vector(embedding), service="neo4j"
        )
        fts = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_business_terms_fulltext(search_query), service="neo4j"
        )
        return table_discovery.merge_business_terms(v, fts)

    async def _intents() -> list:
        return await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_intents(embedding), service="neo4j"
        )

    templates_merged, business_terms, intents, memory_context = await asyncio.gather(
        _templates(),
        _business_terms(),
        _intents(),
        long_term.retrieve_user_memory(user_id, search_query),
    )

    logger.info(
        "context_fetcher | group_a done | business_terms={} | intents={}",
        [b.get("term") for b in business_terms],
        [i.get("name") for i in intents],
    )
    return templates_merged, business_terms, intents, memory_context


async def _fetch_group_b(
    tables: list[dict],
    embedding: list[float],
    search_query: str,
    entity_tokens: list[str] | None = None,
) -> tuple:
    """Group B: table enrichment that depends on table discovery results.

    Column loading has been removed from this phase — it is deferred to schema_enricher
    which runs AFTER anchor_resolver identifies the specific anchor tables. This eliminates
    the GLOBAL_CAP truncation problem where anchor table columns (e.g. lpp.borrowing.repayment_date)
    were cut by ranking against 14 unrelated tables.

    This group now handles:
    - Cross-domain detection (which hub table bridges domains)
    - Join-critical column identification (fast, needed for display ordering)
    - Entity hint extraction (direct value matches from entity_value discovery path)

    Column loading (display_columns + _column_lookup) runs in schema_enricher after
    anchor_resolver identifies the 2-4 relevant anchor tables.
    """
    # Cross-domain detection (sync, 1-3 Neo4j queries depending on outcome)
    tables, hub_info, is_cross_domain = await asyncio.to_thread(cross_domain.detect, tables)

    # Prioritize hub at front of list — ensures it's within anchor_resolver's table window
    if hub_info and hub_info.get("hub_table_fqn"):
        hub_fqn = hub_info["hub_table_fqn"]
        hub_entry = next((t for t in tables if t.get("fqn") == hub_fqn), None)
        if hub_entry:
            tables = [hub_entry] + [t for t in tables if t.get("fqn") != hub_fqn]
            logger.info("context_fetcher | hub_prioritized | fqn={}", hub_fqn)

    # Join-critical columns — needed for anchor_resolver context and later schema_enricher ordering
    join_crit_cols = await asyncio.to_thread(column_loader.get_join_critical_cols, tables)

    # Collect entity hints from entity_value-discovered tables.
    # UUID columns are excluded — they are internal row identifiers, never filter targets.
    entity_hints = [
        {
            "table_fqn": t["fqn"],
            "column": t["entity_matched_column"],
            "matched_value": t["entity_matched_value"],
            "token": t["entity_matched_token"],
            "match_score": t.get("entity_match_score", 0),
        }
        for t in tables
        if t.get("entity_matched_column") and t.get("fqn")
        and not column_loader._is_uuid_col(t["entity_matched_column"])
        and (t.get("entity_match_score") or 0) >= 2   # require actual value match, not description hit
    ]

    # Load a minimal fallback column set for the legacy intent_resolver path.
    # When anchor_resolver SUCCEEDS → schema_enricher loads complete columns for anchor tables.
    # When anchor_resolver FAILS → intent_resolver uses this fallback context.
    # We load only T1 (join-critical) + top semantic matches to keep it fast and non-redundant.
    # This is intentionally lighter than the old full column load — just enough for intent_resolver
    # to identify measures, filters, and join keys without hallucinating.
    display_columns, col_lookup, entity_col_tables = await asyncio.to_thread(
        column_loader.load_and_prioritize, tables, embedding, search_query, join_crit_cols, entity_tokens
    )

    return tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints, entity_col_tables


# ── W2 pure-string helpers (no re import) ─────────────────────────────────────

def _concept_before_operator(line: str) -> str:
    """Extract concept from CONDITION line — text before the first operator or numeric."""
    body = line.split(":", 1)[1].strip() if ":" in line else line
    for prefix in ("Highlight (flag)", "Highlight", "Filter —", "Filter:", "Flag"):
        if body.lower().startswith(prefix.lower()):
            body = body[len(prefix):].strip()
    for op in (" < ", " > ", " = ", "$", "<", ">"):
        if op in body:
            body = body[:body.index(op)]
    parts = [p for p in body.replace("_", " ").split() if len(p) >= 3]
    return " ".join(parts[-3:]) if parts else ""


def _first_noun_phrase(line: str) -> str:
    """First 3 meaningful words after the label prefix."""
    body = line.split(":", 1)[1].strip() if ":" in line else line
    parts = [p for p in body.split() if len(p) >= 3]
    return " ".join(parts[:3])


def _concept_before_vs(line: str) -> str:
    """Text before ' vs ' or ' against ' in a COMPARISON line."""
    body = line.split(":", 1)[1].strip() if ":" in line else line
    for sep in (" vs ", " against ", " versus "):
        if sep in body.lower():
            idx = body.lower().index(sep)
            return body[:idx].strip()
    return _first_noun_phrase(line)

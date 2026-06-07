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


async def context_fetcher(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("context_fetcher START | thread={} | question={}", state["thread_id"], state["question"][:80])

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

        # ── Embed question (Cohere, Redis-cached) ──────────────────────────────
        embedding = await retry_async(lambda: helpers.get_embedding(search_query), service="redis")
        tokens    = helpers.tokenize_with_bigrams(search_query)
        domain_detected = helpers.domain_keyword_detected(search_query)

        # ── Table discovery: all paths in parallel ────────────────────────────
        # Returns (tables, pinned_fqns, bt_pin_data) — pinned_fqns survive the cap
        tables, pinned_fqns, _bt_pin_data = await table_discovery.run_8_path_discovery(
            embedding, tokens, search_query, domain_detected
        )

        if not tables:
            logger.warning("context_fetcher | NO tables found | thread={}", state["thread_id"])
            return {"error": "semantic_layer_unavailable", "semantic_context": None}

        # ── Groups A + B run in parallel ───────────────────────────────────────
        # Group A: independent of table results — templates, terms, intents, memory
        # Group B: depends on tables — cross-domain, join-critical cols, column loading
        (group_a, group_b) = await asyncio.gather(
            _fetch_group_a(embedding, search_query, state["user_id"]),
            _fetch_group_b(tables, embedding, search_query),
        )

        templates_merged, business_terms, intents, memory_context = group_a
        tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints = group_b

        # ── Trim for LLM display (join-critical gets full descriptions) ────────
        templates_trimmed = helpers.trim_objects(templates_merged)
        tables_trimmed    = helpers.trim_objects(tables)
        columns_trimmed   = helpers.trim_objects(display_columns, join_critical_cols=join_crit_cols)

        # ── Assemble SemanticContext ───────────────────────────────────────────
        semantic_context = {
            "templates":          templates_trimmed,
            "tables":             tables_trimmed,
            "columns":            columns_trimmed,
            "_column_lookup":     col_lookup,
            "join_critical_cols": list(join_crit_cols),
            "business_terms":     business_terms,
            "intents":            intents,
            "is_cross_domain":    is_cross_domain,
            "cross_domain_hub":   hub_info,
            "session_summary":    session_summary,
            "memory_context":     memory_context,
            "effective_question": effective_question,
            "is_followup":        is_followup,
            "entity_hints":       entity_hints,
        }

        tables_found  = [t["fqn"] for t in tables_trimmed if t.get("fqn")]
        columns_found = len(columns_trimmed)

        if not tables_found:
            logger.warning("context_fetcher | NO tables in context | thread={}", state["thread_id"])

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | tables={} | cols={} | "
            "is_cross_domain={} | hub={}",
            state["thread_id"], is_followup, tables_found, columns_found,
            is_cross_domain, hub_info.get("hub_table_fqn") if hub_info else "none",
        )
        return {"semantic_context": semantic_context, "effective_question": effective_question, "error": None}

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
        }
        for t in tables
        if t.get("entity_matched_column") and t.get("fqn")
        and not column_loader._is_uuid_col(t["entity_matched_column"])
    ]

    # Load a minimal fallback column set for the legacy intent_resolver path.
    # When anchor_resolver SUCCEEDS → schema_enricher loads complete columns for anchor tables.
    # When anchor_resolver FAILS → intent_resolver uses this fallback context.
    # We load only T1 (join-critical) + top semantic matches to keep it fast and non-redundant.
    # This is intentionally lighter than the old full column load — just enough for intent_resolver
    # to identify measures, filters, and join keys without hallucinating.
    display_columns, col_lookup = await asyncio.to_thread(
        column_loader.load_and_prioritize, tables, embedding, search_query, join_crit_cols
    )

    return tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints

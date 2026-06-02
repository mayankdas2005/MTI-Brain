"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. No Redshift. Embeds the question, runs 8-path table discovery,
detects cross-domain queries, loads columns with join-critical prioritization,
and assembles SemanticContext for downstream agents.
"""

from __future__ import annotations

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
        session_summary = short_term.get_session_summary(state["thread_id"])
        raw_question    = state["question"]
        is_followup     = helpers.is_followup_question(raw_question, bool(session_summary))

        if is_followup:
            previous_follow_ups = state.get("follow_ups") or []
            search_query = helpers.reconstruct_question(
                raw_question, session_summary or "", previous_follow_ups
            )
            logger.info("context_fetcher | follow-up reconstructed | search={}", search_query[:80])
        else:
            search_query = raw_question

        # ── Embed question (Cohere, Redis-cached) ──────────────────────────────
        embedding = await retry_async(lambda: helpers.get_embedding(search_query), service="redis")
        tokens    = helpers.tokenize_with_bigrams(search_query)
        domain_detected = helpers.domain_keyword_detected(search_query)

        # ── 8-path table discovery (all Neo4j, no templates in paths) ─────────
        tables = await table_discovery.run_8_path_discovery(
            embedding, tokens, search_query, domain_detected
        )

        if not tables:
            logger.warning("context_fetcher | NO tables found | thread={}", state["thread_id"])
            return {"error": "semantic_layer_unavailable", "semantic_context": None}

        # ── QueryTemplate lookup — hints ONLY, after tables determined ─────────
        templates_v   = retry_sync(lambda: neo4j_client.search_query_templates(embedding), service="neo4j")
        templates_fts = retry_sync(lambda: neo4j_client.search_query_templates_fulltext(search_query), service="neo4j")
        templates_merged = table_discovery.merge_template_results(templates_v, templates_fts)

        # ── Cross-domain detection (4-method cascade) ─────────────────────────
        tables, hub_info, is_cross_domain = cross_domain.detect(tables)

        # ── Join-critical columns: Sources A+B+C+D ────────────────────────────
        join_crit_cols = column_loader.get_join_critical_cols(tables)

        # ── Column loading + prioritization ───────────────────────────────────
        display_columns, col_lookup = column_loader.load_and_prioritize(
            tables, embedding, search_query, join_crit_cols
        )

        # ── Business terms + intents ───────────────────────────────────────────
        business_terms_v   = retry_sync(lambda: neo4j_client.search_business_terms_vector(embedding), service="neo4j")
        business_terms_fts = retry_sync(lambda: neo4j_client.search_business_terms_fulltext(search_query), service="neo4j")
        business_terms     = table_discovery.merge_business_terms(business_terms_v, business_terms_fts)
        intents            = retry_sync(lambda: neo4j_client.search_intents(embedding), service="neo4j")

        logger.info("context_fetcher | business_terms={} | intents={}",
                    [b.get("term") for b in business_terms], [i.get("name") for i in intents])

        # ── Long-term memory ───────────────────────────────────────────────────
        memory_context = await long_term.retrieve_user_memory(state["user_id"], search_query)

        # ── Trim for LLM display (join-critical gets full descriptions) ────────
        templates_trimmed = helpers.trim_objects(templates_merged)
        tables_trimmed    = helpers.trim_objects(tables)
        # For columns: pass join_crit_cols so descriptions aren't truncated for join columns
        columns_trimmed   = helpers.trim_objects(display_columns, join_critical_cols=join_crit_cols)

        # ── Assemble SemanticContext ───────────────────────────────────────────
        semantic_context = {
            "templates":          templates_trimmed,   # hints only — NOT a discovery path
            "tables":             tables_trimmed,
            "columns":            columns_trimmed,      # trimmed for LLM display
            "_column_lookup":     col_lookup,           # full untrimmed: filter_resolver + ir_utils
            "join_critical_cols": list(join_crit_cols), # for intent_resolver column display
            "business_terms":     business_terms,
            "intents":            intents,
            "is_cross_domain":    is_cross_domain,
            "cross_domain_hub":   hub_info,
            "session_summary":    session_summary,
            "memory_context":     memory_context,
            "effective_question": search_query if is_followup else None,
            "is_followup":        is_followup,
        }

        tables_found   = [t["fqn"] for t in tables_trimmed if t.get("fqn")]
        columns_found  = len(columns_trimmed)

        if not tables_found:
            logger.warning("context_fetcher | NO tables in context | thread={}", state["thread_id"])

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | tables={} | cols={} | "
            "is_cross_domain={} | hub={}",
            state["thread_id"], is_followup, tables_found, columns_found,
            is_cross_domain, hub_info.get("hub_table_fqn") if hub_info else "none",
        )
        return {"semantic_context": semantic_context, "error": None}

    except Exception as e:
        logger.error("context_fetcher FAILED | thread={} | error={}", state["thread_id"], e)
        return {"error": "semantic_layer_unavailable", "semantic_context": None}

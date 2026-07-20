"""Routing functions for the Neo4j analytics LangGraph pipeline.

All routing decisions live here — one function per conditional edge.
"""

from __future__ import annotations

from langgraph.graph import END
from langgraph.types import RetryPolicy

from app.core.config import settings
from app.core.logger import logger
from app.services.agents.nodes.compress import SUMMARIZE_THRESHOLD
from app.services.agents.node_names import (
    ANCHOR_RESOLVER as N_ANCHOR_RESOLVER,
    CHART_AGENT as N_CHART_AGENT,
    COMPRESS as N_COMPRESS,
    CONTEXT_FETCHER as N_CONTEXT_FETCHER,
    DATA_QUALITY_CHECKER as N_DATA_QUALITY_CHECKER,
    DEEP_SENSITIVITY as N_DEEP_SENSITIVITY,
    DIRECTIVE_WRITER as N_DIRECTIVE_WRITER,
    ERROR_RESPONSE as N_ERROR_RESPONSE,
    EXECUTOR as N_EXECUTOR,
    FILTER_RESOLVER as N_FILTER_RESOLVER,
    GENERAL_CHAT as N_GENERAL_CHAT,
    INTENT_ASSEMBLER as N_INTENT_ASSEMBLER,
    INTENT_RESOLVER as N_INTENT_RESOLVER,
    QUERY_COMPILER as N_QUERY_COMPILER,
    QUERY_PLANNER as N_QUERY_PLANNER,
    SCHEMA_ENRICHER as N_SCHEMA_ENRICHER,
    SQL_GENERATOR as N_SQL_GENERATOR,
    SQL_VALIDATOR as N_SQL_VALIDATOR,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.agents.state import AnalyticsState

MAX_RECOMPILE = 3
MAX_REPAIR = 3
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)


def route_intake(state: AnalyticsState) -> str:
    if state.get("error"):
        logger.info("route: intake → error_response | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    qt = state.get("question_type", "analytics")
    if qt == "general_chat":
        logger.info("route: intake → general_chat | thread={}", state["thread_id"])
        return N_GENERAL_CHAT
    logger.info("route: intake → context_fetcher | thread={}", state["thread_id"])
    return N_CONTEXT_FETCHER


def route_after_context_fetcher(state: AnalyticsState) -> str:
    if state.get("error") == "semantic_layer_unavailable":
        logger.info("route: context_fetcher → error_response | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    logger.info("route: context_fetcher → anchor_resolver | thread={}", state["thread_id"])
    return N_ANCHOR_RESOLVER


def route_after_anchor_resolver(state: AnalyticsState) -> str:
    """Route after anchor_resolver — to schema_enricher (success) or legacy intent_resolver (fallback)."""
    anchor_tables = state.get("anchor_tables_resolved") or []
    if not anchor_tables:
        logger.warning("route: anchor_resolver → intent_resolver (fallback — no tables resolved) | thread={}", state["thread_id"])
        return N_INTENT_RESOLVER
    logger.info("route: anchor_resolver → query_planner | tables={} | thread={}", anchor_tables, state["thread_id"])
    return N_QUERY_PLANNER


def route_after_intent_assembler(state: AnalyticsState) -> str:
    """Route after intent_assembler — to directive_writer (success) or legacy intent_resolver (fallback)."""
    if state.get("_intent_assembler_fallback"):
        logger.warning("route: intent_assembler → intent_resolver (fallback) | thread={}", state["thread_id"])
        return N_INTENT_RESOLVER
    resolved = state.get("resolved_intent") or {}
    if not resolved.get("anchor_tables") and not resolved.get("measures"):
        logger.warning("route: intent_assembler → intent_resolver (empty resolved_intent) | thread={}", state["thread_id"])
        return N_INTENT_RESOLVER
    logger.info("route: intent_assembler → directive_writer | thread={}", state["thread_id"])
    return N_DIRECTIVE_WRITER


def route_intent(state: AnalyticsState) -> str:
    logger.info("route: intent_resolver/directive_writer → query_compiler | thread={}", state["thread_id"])
    return N_QUERY_COMPILER


def route_compiler(state: AnalyticsState) -> str:
    if state.get("error") and not state.get("semantic_ir_list"):
        logger.info("route: query_compiler → error_response (IR build failed) | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    # M7: always route through filter_resolver so filter_directive is always written.
    # When filter_resolution_needed=False, filter_resolver does only string-building (fast).
    # Without this, sql_generator receives no FILTER DIRECTIVE and must guess WHERE predicates.
    logger.info(
        "route: query_compiler → filter_resolver | resolution_needed={} | thread={}",
        state.get("filter_resolution_needed", False), state["thread_id"],
    )
    return N_FILTER_RESOLVER


def route_filter_resolver(state: AnalyticsState) -> str:
    logger.info("route: filter_resolver → sql_generator | thread={}", state["thread_id"])
    return N_SQL_GENERATOR


def route_validator(state: AnalyticsState) -> str:
    if state.get("error"):
        repair_count = state.get("repair_count", 0)
        recompile_count = state.get("recompile_count", 0)
        if repair_count > 0:
            logger.info(
                "route: sql_validator → error_response (in repair loop, skipping recompile) | repair={} | thread={}",
                repair_count, state["thread_id"],
            )
            return N_ERROR_RESPONSE
        if recompile_count < MAX_RECOMPILE:
            logger.info(
                "route: sql_validator → sql_generator (recompile #{}) | thread={}",
                recompile_count + 1, state["thread_id"],
            )
            return N_SQL_GENERATOR
        logger.info("route: sql_validator → error_response (max recompiles reached) | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    logger.info("route: sql_validator → executor | thread={}", state["thread_id"])
    return N_EXECUTOR


def route_executor(state: AnalyticsState) -> str:
    repair_count = state.get("repair_count", 0)
    # Post-execution always enters deep analysis enrichment chain first;
    # deep_sensitivity self-gates and is a no-op when deep_analysis=False.
    # DQ checker runs before the deep chain when enabled.
    _next_after_exec = N_DATA_QUALITY_CHECKER if settings.DATA_QUALITY_CHECKER_ENABLED else N_DEEP_SENSITIVITY

    if state.get("stopped"):
        logger.info("route: executor → {} (stopped) | thread={}", _next_after_exec, state["thread_id"])
        return _next_after_exec

    if state.get("error") and repair_count >= MAX_REPAIR:
        logger.info("route: executor → {} (repairs exhausted) | thread={}", _next_after_exec, state["thread_id"])
        return _next_after_exec

    if repair_count > state.get("_prev_repair_count", -1):
        logger.info("route: executor → sql_validator (after repair) | thread={}", state["thread_id"])
        return N_SQL_VALIDATOR

    logger.info("route: executor → {} | thread={}", _next_after_exec, state["thread_id"])
    return _next_after_exec


def route_synthesis(state: AnalyticsState) -> str:
    result_list = state.get("result_list") or []
    has_rows = any(r.get("rows") for r in result_list)
    if has_rows and not state.get("no_data"):
        logger.info("route: synthesis → chart_agent | thread={}", state["thread_id"])
        return N_CHART_AGENT
    logger.info("route: synthesis → compress_check (no data for chart) | thread={}", state["thread_id"])
    return N_COMPRESS if _should_compress(state) else END


def _should_compress(state: AnalyticsState) -> bool:
    return len(state.get("messages") or []) >= SUMMARIZE_THRESHOLD


def route_should_compress(state: AnalyticsState) -> str:
    if _should_compress(state):
        logger.info("route: → compress | thread={} | messages={}", state["thread_id"], len(state.get("messages") or []))
        return N_COMPRESS
    return END

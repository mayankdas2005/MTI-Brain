"""Routing functions for the Neo4j analytics LangGraph pipeline.

All routing decisions live here — one function per conditional edge.
"""

from __future__ import annotations

from langgraph.graph import END
from langgraph.types import RetryPolicy

from app.core.logger import logger
from app.services.neo4j_analytics.nodes.compress import SUMMARIZE_THRESHOLD
from app.services.neo4j_analytics.node_names import (
    CHART_AGENT as N_CHART_AGENT,
    COMPRESS as N_COMPRESS,
    CONTEXT_FETCHER as N_CONTEXT_FETCHER,
    ERROR_RESPONSE as N_ERROR_RESPONSE,
    EXECUTOR as N_EXECUTOR,
    FILTER_RESOLVER as N_FILTER_RESOLVER,
    GENERAL_CHAT as N_GENERAL_CHAT,
    INTENT_RESOLVER as N_INTENT_RESOLVER,
    QUERY_COMPILER as N_QUERY_COMPILER,
    SQL_VALIDATOR as N_SQL_VALIDATOR,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.neo4j_analytics.state import AnalyticsState

MAX_RECOMPILE = 3
MAX_REPAIR = 2
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)


def route_intake(state: AnalyticsState) -> str:
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
    logger.info("route: context_fetcher → intent_resolver | thread={}", state["thread_id"])
    return N_INTENT_RESOLVER


def route_intent(state: AnalyticsState) -> str:
    logger.info("route: intent_resolver → query_compiler | thread={}", state["thread_id"])
    return N_QUERY_COMPILER


def route_compiler(state: AnalyticsState) -> str:
    if state.get("filter_resolution_needed"):
        logger.info("route: query_compiler → filter_resolver | thread={}", state["thread_id"])
        return N_FILTER_RESOLVER
    logger.info("route: query_compiler → sql_validator | thread={}", state["thread_id"])
    return N_SQL_VALIDATOR


def route_filter_resolver(state: AnalyticsState) -> str:
    logger.info("route: filter_resolver → sql_validator | thread={}", state["thread_id"])
    return N_SQL_VALIDATOR


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
                "route: sql_validator → query_compiler (recompile #{}) | thread={}",
                recompile_count + 1, state["thread_id"],
            )
            return N_QUERY_COMPILER
        logger.info("route: sql_validator → error_response (max recompiles reached) | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    logger.info("route: sql_validator → executor | thread={}", state["thread_id"])
    return N_EXECUTOR


def route_executor(state: AnalyticsState) -> str:
    repair_count = state.get("repair_count", 0)

    if state.get("stopped"):
        logger.info("route: executor → synthesis (stopped) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if state.get("error") and repair_count >= MAX_REPAIR:
        logger.info("route: executor → synthesis (repairs exhausted) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if repair_count > state.get("_prev_repair_count", -1):
        logger.info("route: executor → sql_validator (after repair) | thread={}", state["thread_id"])
        return N_SQL_VALIDATOR

    logger.info("route: executor → synthesis | thread={}", state["thread_id"])
    return N_SYNTHESIS


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

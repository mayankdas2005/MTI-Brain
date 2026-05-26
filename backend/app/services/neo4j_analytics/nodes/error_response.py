"""Error response node — surfaces user-friendly message on infrastructure failure."""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.state import AnalyticsState

_ERROR_MESSAGES = {
    "semantic_layer_unavailable": (
        "I'm having trouble accessing the data catalog right now. "
        "Please try again in a moment."
    ),
    "data_unavailable": (
        "I'm unable to reach the data warehouse right now. "
        "Your query has been logged and you can retry shortly."
    ),
    "default": (
        "Something went wrong while processing your question. "
        "Please try again."
    ),
}


async def error_response(state: AnalyticsState, config: RunnableConfig) -> dict:
    error_code = state.get("error", "default")
    message = _ERROR_MESSAGES.get(error_code, _ERROR_MESSAGES["default"])
    logger.warning("error_response | thread={} | error_code={}", state["thread_id"], error_code)
    return {
        "answer": message,
        "follow_ups": [],
        "stopped": True,
    }

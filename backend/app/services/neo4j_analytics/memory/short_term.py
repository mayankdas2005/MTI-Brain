"""Short-term session memory for the analytics pipeline.

Uses AsyncPostgresSaver checkpoints (same pool as the main pipeline).
Compresses conversation after 6+ messages using Haiku summary.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.neo4j_analytics import redis_client


async def compress_session(
    messages: list,
    thread_id: str,
    llm,
) -> str:
    """Compress conversation messages into a short summary.

    Returns the summary string. Also caches in Redis (30 min TTL).
    """
    from langchain_core.messages import HumanMessage, AIMessage

    if len(messages) < 6:
        return ""

    conversation_text = []
    for m in messages[-12:]:
        if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human":
            conversation_text.append(f"User: {(m.content or '')[:300]}")
        else:
            conversation_text.append(f"Assistant: {(m.content or '')[:300]}")

    conversation = "\n".join(conversation_text)

    try:
        from app.services.neo4j_analytics.prompts import COMPRESS_PROMPT
        from langchain_core.messages import HumanMessage as HM
        response = await llm.ainvoke(
            COMPRESS_PROMPT.format_messages(conversation=conversation)
        )
        summary = (response.content or "").strip()
        if summary:
            redis_client.set_session_summary(thread_id, summary, ttl=1800)
            logger.info("short_term | compressed session | thread={} | summary_len={}", thread_id, len(summary))
        return summary
    except Exception as e:
        logger.warning("short_term | compress failed | thread={} | error={}", thread_id, e)
        return ""


def get_session_summary(thread_id: str) -> str:
    """Retrieve cached session summary."""
    return redis_client.get_session_summary(thread_id) or ""

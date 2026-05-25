"""Long-term cross-session memory for the analytics pipeline.

Uses existing PostgresStore (langgraph_store_* tables) — no new infrastructure.
Retrieves top 3 similar past interactions per user as memory context.
"""

from __future__ import annotations

import json

from app.core.logger import logger

_memory_store = None


def set_memory_store(store) -> None:
    """Called by graph.py after init to share the existing store."""
    global _memory_store
    _memory_store = store


def get_memory_store():
    return _memory_store


async def retrieve_user_memory(user_id: str, question: str, limit: int = 3) -> str:
    """Return top-N similar past interactions as a formatted string for LLM context."""
    store = get_memory_store()
    if not store or not user_id:
        return ""

    try:
        import asyncio
        results = await asyncio.to_thread(
            store.search,
            (str(user_id), "mti_queries"),
            query=question,
            limit=limit,
        )
        if not results:
            return ""

        parts = []
        for item in results:
            value = item.value if hasattr(item, "value") else (item if isinstance(item, dict) else {})
            q = value.get("question", "")[:120]
            intent = value.get("intent", "")
            rows = value.get("row_count", 0)
            if q:
                parts.append(f"- Q: {q} | intent: {intent} | rows: {rows}")

        return "\n".join(parts) if parts else ""
    except Exception as e:
        logger.warning("long_term | retrieve failed | user={} | error={}", user_id, e)
        return ""


async def save_user_memory(
    user_id: str,
    thread_id: str,
    question: str,
    answer_summary: str,
    intent: str,
    row_count: int,
    sql: str = "",
) -> None:
    """Save a successful interaction to long-term memory."""
    store = get_memory_store()
    if not store or not user_id:
        return

    try:
        import asyncio
        payload = {
            "question": question,
            "answer_summary": answer_summary[:500],
            "intent": intent,
            "row_count": row_count,
            "sql": sql[:1000],
        }
        await asyncio.to_thread(
            store.put,
            (str(user_id), "mti_queries"),
            str(thread_id),
            payload,
        )
        logger.info("long_term | saved | user={} | thread={} | intent={}", user_id, thread_id, intent)
    except Exception as e:
        logger.warning("long_term | save failed | user={} | error={}", user_id, e)

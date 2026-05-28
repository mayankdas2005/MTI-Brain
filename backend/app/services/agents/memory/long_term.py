"""Long-term cross-session memory for the analytics pipeline.

Uses existing PostgresStore (langgraph_store_* tables) — no new infrastructure.
Retrieves top 3 similar past interactions per user as memory context.
"""

from __future__ import annotations

import asyncio

from app.core.logger import logger

_memory_store = None
_conninfo: str | None = None
_embed_fn = None


def set_memory_store(store, conninfo: str | None = None, embed_fn=None) -> None:
    global _memory_store, _conninfo, _embed_fn
    _memory_store = store
    if conninfo:
        _conninfo = conninfo
    if embed_fn:
        _embed_fn = embed_fn


def get_memory_store():
    return _memory_store


def _try_reconnect() -> bool:
    """Attempt to reinitialize the store when the connection has closed."""
    global _memory_store
    if not _conninfo or not _embed_fn:
        return False
    try:
        from contextlib import ExitStack
        from langgraph.store.postgres import PostgresStore
        from langgraph.store.base import IndexConfig
        store = ExitStack().enter_context(
            PostgresStore.from_conn_string(
                _conninfo,
                index=IndexConfig(embed=_embed_fn, dims=1536),
            )
        )
        _memory_store = store
        logger.info("long_term | reconnected to memory store")
        return True
    except Exception as e:
        logger.warning("long_term | reconnect failed | error={}", e)
        return False


def _is_connection_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "connection" in msg or "closed" in msg or "not open" in msg


async def retrieve_user_memory(user_id: str, question: str, limit: int = 3) -> str:
    """Return top-N similar past interactions as a formatted string for LLM context."""
    store = get_memory_store()
    if not store or not user_id:
        return ""

    for attempt in range(2):
        try:
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
            if attempt == 0 and _is_connection_error(e):
                logger.warning("long_term | connection closed, reconnecting | user={}", user_id)
                if _try_reconnect():
                    store = get_memory_store()
                    continue
            logger.warning("long_term | retrieve failed | user={} | error={}", user_id, e)
            return ""

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

    for attempt in range(2):
        try:
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
            return

        except Exception as e:
            if attempt == 0 and _is_connection_error(e):
                logger.warning("long_term | connection closed on save, reconnecting | user={}", user_id)
                if _try_reconnect():
                    store = get_memory_store()
                    continue
            logger.warning("long_term | save failed | user={} | error={}", user_id, e)
            return

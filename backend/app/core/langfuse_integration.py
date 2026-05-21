"""Langfuse observability integration.

Initializes the Langfuse client once at startup and provides helpers
for callback handlers and context propagation. Degrades gracefully
when disabled or unreachable.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logger import logger

_langfuse_client = None
_enabled = False


def init_langfuse() -> None:
    global _langfuse_client, _enabled

    if not settings.LANGFUSE_ENABLED:
        logger.info("Langfuse disabled (LANGFUSE_ENABLED=false)")
        return

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        _enabled = True
        logger.info(f"Langfuse initialized → {settings.LANGFUSE_HOST}")
    except Exception as e:
        logger.warning(f"Langfuse init failed (tracing disabled): {e}")
        _enabled = False


def shutdown_langfuse() -> None:
    global _langfuse_client, _enabled
    if _langfuse_client:
        try:
            _langfuse_client.flush()
            _langfuse_client.shutdown()
            logger.info("Langfuse shut down")
        except Exception as e:
            logger.warning(f"Langfuse shutdown error: {e}")
    _langfuse_client = None
    _enabled = False


def get_langfuse_client():
    return _langfuse_client


def is_enabled() -> bool:
    return _enabled


def create_callback_handler():
    """Create a LangChain CallbackHandler for Langfuse. Returns None if disabled."""
    if not _enabled:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as e:
        logger.warning(f"Failed to create Langfuse callback: {e}")
        return None


def langfuse_context(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
):
    """Return a propagate_attributes context manager. No-op when disabled."""
    if not _enabled:
        from contextlib import nullcontext

        return nullcontext()

    try:
        from langfuse import propagate_attributes

        kwargs = {}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if tags:
            kwargs["tags"] = tags
        if metadata:
            kwargs["metadata"] = metadata
        return propagate_attributes(**kwargs)
    except Exception as e:
        logger.warning(f"Failed to create Langfuse context: {e}")
        from contextlib import nullcontext

        return nullcontext()


def score_trace(
    *,
    trace_id: str | None = None,
    name: str = "user-feedback",
    value: float,
    comment: str | None = None,
) -> None:
    """Send a feedback score to a specific Langfuse trace."""
    if not _enabled or not _langfuse_client or not trace_id:
        return

    try:
        _langfuse_client.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment,
        )
    except Exception as e:
        logger.warning(f"Failed to send score to Langfuse: {e}")

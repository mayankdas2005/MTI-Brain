"""Langfuse observability integration — Langfuse Python SDK v3.

Initializes the Langfuse client once at startup and provides helpers
for callback handlers and context propagation. Degrades gracefully
when disabled or unreachable.

v3 migration notes
------------------
* Credentials are passed via env vars; ``get_client()`` returns the singleton.
* ``Langfuse.trace()`` / ``Langfuse.score()`` instance methods are gone.
  Scoring uses ``get_client().create_score()``; making a trace public uses the
  ingestion REST API (``/api/public/ingestion``) because
  ``set_current_trace_as_public()`` only works inside an ``@observe`` context,
  which we don't have when tracing via ``CallbackHandler``.
* Flushing uses ``get_client().flush()`` — never ``lf_handler.langfuse.flush()``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.logger import logger

_enabled = False


def _register_bedrock_model_pricing() -> None:
    """Register AWS Bedrock Claude model pricing in Langfuse using regex match patterns.

    The LangChain Bedrock callback reports the full ARN or model ID as the model
    name (e.g. ``arn:aws:bedrock:us-west-2::…/us.anthropic.claude-3-5-sonnet-…``).
    Langfuse needs a matching entry in its model table to calculate cost.

    All pricing data is sourced from ``token_tracker.MODELS`` — that module is
    the single source of truth for costs.  Silently skips models that already
    exist (HTTP 409) so restarts are idempotent.
    """
    # Lazy import to avoid a core→services circular dependency at module level.
    from app.services.agents.token_tracker import MODELS

    auth = (settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY)
    base_url = settings.LANGFUSE_BASE_URL.rstrip("/")
    registered = 0

    for pricing_key, model_data in MODELS.items():
        if "lf_pattern" not in model_data:
            continue
        try:
            resp = httpx.post(
                f"{base_url}/api/public/models",
                auth=auth,
                json={
                    "modelName":    model_data["lf_name"],
                    "matchPattern": model_data["lf_pattern"],
                    "unit":         "TOKENS",
                    "inputPrice":   round(model_data["input"]  / 1_000_000, 10),
                    "outputPrice":  round(model_data["output"] / 1_000_000, 10),
                },
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                registered += 1
            elif resp.status_code in (400, 409):
                pass  # already registered — idempotent
            else:
                logger.warning(
                    f"Langfuse model registration '{model_data['lf_name']}': "
                    f"HTTP {resp.status_code} {resp.text[:120]}"
                )
        except Exception as e:
            logger.warning(f"Langfuse model registration '{pricing_key}' failed: {e}")

    if registered:
        logger.info(f"Langfuse: registered {registered} Bedrock model pricing definitions")


def init_langfuse() -> None:
    global _enabled

    if not settings.LANGFUSE_ENABLED:
        logger.info("Langfuse disabled (LANGFUSE_ENABLED=false)")
        return

    try:
        # v3 SDK reads credentials from environment variables.
        # Set them explicitly from settings so they work regardless of whether
        # the caller already has them in the environment.
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
        os.environ["LANGFUSE_BASE_URL"] = settings.LANGFUSE_BASE_URL

        from langfuse import get_client

        client = get_client()
        if not client.auth_check():
            raise RuntimeError(
                "auth_check() returned False — verify LANGFUSE_PUBLIC_KEY, "
                "LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL"
            )

        _enabled = True
        logger.info(f"Langfuse initialized → {settings.LANGFUSE_BASE_URL}")
        _register_bedrock_model_pricing()
    except Exception as e:
        logger.warning(f"Langfuse init failed (tracing disabled): {e}")
        _enabled = False


def shutdown_langfuse() -> None:
    global _enabled
    if _enabled:
        try:
            from langfuse import get_client
            get_client().flush()
            logger.info("Langfuse shut down")
        except Exception as e:
            logger.warning(f"Langfuse shutdown error: {e}")
    _enabled = False


def get_langfuse_client():
    """Return the active Langfuse v3 client, or None when disabled."""
    if not _enabled:
        return None
    try:
        from langfuse import get_client
        return get_client()
    except Exception:
        return None


def is_enabled() -> bool:
    return _enabled


def flush_langfuse() -> None:
    """Flush pending Langfuse events. Call at the end of each pipeline run."""
    if not _enabled:
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception as e:
        logger.warning(f"Langfuse flush error: {e}")


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

        kwargs: dict = {}
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


def make_trace_public(trace_id: str) -> str | None:
    """Mark a trace as publicly shareable and return its URL.

    ``set_current_trace_as_public()`` only works inside an ``@observe`` context.
    Since we trace via ``CallbackHandler``, we update the trace directly through
    the Langfuse ingestion REST API instead.

    The URL is obtained from ``get_client().get_trace_url()`` which includes the
    correct project slug for both cloud and self-hosted instances.
    """
    if not _enabled or not trace_id:
        return None
    try:
        httpx.post(
            f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/api/public/ingestion",
            auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
            json={
                "batch": [{
                    "id":        str(uuid.uuid4()),
                    "type":      "trace-create",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "body":      {"id": trace_id, "public": True},
                }]
            },
            timeout=5.0,
        )
    except Exception as e:
        logger.warning(f"Failed to mark trace {trace_id} public: {e}")

    # get_trace_url() constructs the canonical URL (includes project slug on cloud).
    try:
        from langfuse import get_client
        return get_client().get_trace_url(trace_id=trace_id)
    except Exception:
        return f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/trace/{trace_id}"


def score_trace(
    *,
    trace_id: str | None = None,
    name: str = "user-feedback",
    value: float,
    comment: str | None = None,
    data_type: str | None = "BOOLEAN",
) -> None:
    """Send a feedback score to a specific Langfuse trace."""
    if not _enabled or not trace_id:
        return

    try:
        from langfuse import get_client
        # Use a deterministic ID so repeated calls overwrite the same score
        # rather than creating duplicate entries.
        score_id = str(uuid.uuid5(uuid.NAMESPACE_X500, f"{trace_id}:{name}"))
        get_client().create_score(
            score_id=score_id,
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment,
            data_type=data_type,
        )
    except Exception as e:
        logger.warning(f"Failed to send score to Langfuse: {e}")

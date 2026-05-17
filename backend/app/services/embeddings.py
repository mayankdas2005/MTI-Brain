"""Shared embedding helpers for MTI Brain.

Single source of truth for Cohere Embed v4 via Bedrock (Bearer token auth).
Used by: feedback service (save + similarity search) and memory store (cross-thread recall).

Provides:
  embed_question()    — async, 1536-dim, search_query input_type
  embed_texts_sync()  — sync, 1536-dim, search_document input_type (for LangGraph IndexConfig)

All async call sites must use embed_question(). embed_texts_sync() is the sole exception:
LangGraph's IndexConfig protocol requires a sync callable — wrap store.put/search in
asyncio.to_thread() at the call site instead.
"""

from __future__ import annotations

import httpx

from app.core.circuit_breaker import embedding_breaker
from app.core.config import settings
from app.core.logger import logger


def _bedrock_embed_url() -> str:
    """Build the Bedrock embedding endpoint from AWS_BEDROCK_COHERE_EMBED_V4_ARN."""
    arn = settings.AWS_BEDROCK_COHERE_EMBED_V4_ARN
    if arn and "inference-profile/" in arn:
        model_id = arn.split("inference-profile/")[-1]
    elif arn:
        model_id = arn
    else:
        model_id = "global.cohere.embed-v4:0"
    model_encoded = model_id.replace(":", "%3A")
    return (
        f"https://bedrock-runtime.{settings.AWS_REGION}.amazonaws.com"
        f"/model/{model_encoded}/invoke"
    )


_EMBED_URL = _bedrock_embed_url()
_AUTH_HEADER = {"Authorization": f"Bearer {settings.AWS_BEARER_TOKEN_BEDROCK}"}
_COMMON_HEADERS = {**_AUTH_HEADER, "Content-Type": "application/json", "Accept": "application/json"}

# ─── Async client (for embed_question) ───────────────────────────────────────

_async_client: httpx.AsyncClient | None = None


async def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(timeout=15.0)
    return _async_client


async def embed_question(text: str) -> list[float] | None:
    """Embed a single question string asynchronously.

    Uses search_query input_type (optimised for retrieval queries).
    Returns 1536-dim float list or None on circuit-breaker open / error.
    """
    if not settings.AWS_BEARER_TOKEN_BEDROCK:
        return None
    try:
        # Check circuit breaker state before making the async call
        if embedding_breaker.current_state == "open":
            logger.warning("Embedding circuit breaker OPEN — skipping embed_question")
            return None
        client = await _get_async_client()
        resp = await client.post(
            _EMBED_URL,
            headers=_COMMON_HEADERS,
            json={
                "texts": [text[:2048]],
                "input_type": "search_query",
                "embedding_types": ["float"],
                "truncate": "END",
            },
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]["float"][0]
    except Exception as e:
        logger.warning(f"embed_question failed: {e}")
        return None


async def close_embedding_client() -> None:
    global _async_client
    if _async_client and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None


# ─── Sync function for LangGraph IndexConfig ─────────────────────────────────

def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """Sync embed for LangGraph PostgresStore IndexConfig.

    Signature required by IndexConfig: list[str] -> list[list[float]].
    Uses search_document input_type (optimised for stored documents).
    Falls back to zero vectors so the store doesn't crash when Bedrock is unavailable.

    IMPORTANT: This function is SYNCHRONOUS and makes blocking HTTP calls.
    Never call it directly from async code — wrap the store.put/store.search
    call in asyncio.to_thread() at the call site.
    """
    if not settings.AWS_BEARER_TOKEN_BEDROCK:
        return [[0.0] * 1536 for _ in texts]
    results: list[list[float]] = []
    try:
        with httpx.Client(timeout=15.0) as client:
            for text in texts:
                try:
                    resp = client.post(
                        _EMBED_URL,
                        headers=_COMMON_HEADERS,
                        json={
                            "texts": [text[:2048]],
                            "input_type": "search_document",
                            "embedding_types": ["float"],
                            "truncate": "END",
                        },
                    )
                    resp.raise_for_status()
                    results.append(resp.json()["embeddings"]["float"][0])
                except Exception as e:
                    logger.warning(f"embed_texts_sync single text failed: {e}")
                    results.append([0.0] * 1536)
    except Exception as e:
        logger.warning(f"embed_texts_sync failed: {e}")
        results = [[0.0] * 1536 for _ in texts]
    return results

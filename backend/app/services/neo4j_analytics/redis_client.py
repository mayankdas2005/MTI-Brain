"""Redis client for the analytics pipeline.

Handles embedding cache, Redshift result cache, and filter value cache.
Redis failures degrade gracefully — the pipeline continues without caching.
"""

from __future__ import annotations

import hashlib
import json
import time

from app.core.circuit_breaker import redis_breaker
from app.core.logger import logger

_redis = None


def init_redis() -> None:
    """Initialize Redis client."""
    global _redis
    try:
        import redis as redis_lib
        from redis import ConnectionPool
        from app.core.config import settings
        pool = ConnectionPool(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
        )
        _redis = redis_lib.Redis(connection_pool=pool)
        _redis.ping()
        logger.info(
            "Redis client initialized | pool={} | health_check={}s",
            settings.REDIS_MAX_CONNECTIONS, settings.REDIS_HEALTH_CHECK_INTERVAL,
        )
    except Exception as e:
        logger.warning("Redis initialization failed (continuing without cache): {}", e)
        _redis = None


def close_redis() -> None:
    global _redis
    if _redis:
        try:
            _redis.close()
        except Exception:
            pass
        _redis = None


def _get_client():
    return _redis


# ── Key helpers ───────────────────────────────────────────────────────────────

def embed_cache_key(text: str, model_version: str = "v4") -> str:
    normalized = text.strip().lower()
    sha = hashlib.sha256(normalized.encode()).hexdigest()
    return f"cohere_embed:{model_version}:{sha}"


def redshift_cache_key(sql: str) -> str:
    sha = hashlib.sha256(sql.encode()).hexdigest()
    return f"redshift:{sha}"


def filter_vals_cache_key(table_fqn: str, col_name: str) -> str:
    return f"filter_vals:{table_fqn}:{col_name}"


def session_summary_key(thread_id: str) -> str:
    return f"session_summary:{thread_id}"


# ── Breaker-protected inner calls ─────────────────────────────────────────────
# Defined at module level so @redis_breaker is applied once, not per call.

@redis_breaker
def _do_get(client, key: str) -> str | None:
    return client.get(key)


@redis_breaker
def _do_set(client, key: str, ttl_seconds: int, value: str) -> None:
    client.setex(key, ttl_seconds, value)


# ── Get / set / delete ────────────────────────────────────────────────────────

def cache_get(key: str) -> str | None:
    client = _get_client()
    if not client:
        return None
    t0 = time.monotonic()
    try:
        val = _do_get(client, key)
        logger.debug("redis get | key={} | hit={} | ms={:.0f}", key[:60], val is not None, (time.monotonic() - t0) * 1000)
        return val
    except Exception as e:
        logger.warning("redis get failed | key={} | error={}", key[:60], e)
        return None


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    client = _get_client()
    if not client:
        return
    try:
        _do_set(client, key, ttl_seconds, value)
        logger.debug("redis set | key={} | ttl={}s", key[:60], ttl_seconds)
    except Exception as e:
        logger.warning("redis set failed | key={} | error={}", key[:60], e)


def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern using SCAN (safe for production)."""
    client = _get_client()
    if not client:
        return 0
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match=pattern, count=100)
            if keys:
                client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.warning("redis delete_pattern failed | pattern={} | error={}", pattern, e)
    return deleted


# ── Typed helpers ─────────────────────────────────────────────────────────────

def get_embedding(text: str, model_version: str = "v4") -> list[float] | None:
    key = embed_cache_key(text, model_version)
    raw = cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def set_embedding(text: str, embedding: list[float], model_version: str = "v4", ttl: int = 7200) -> None:
    key = embed_cache_key(text, model_version)
    cache_set(key, json.dumps(embedding), ttl)


def get_redshift_result(sql: str) -> tuple[list[str], list[list]] | None:
    """Returns (columns, rows) or None."""
    key = redshift_cache_key(sql)
    raw = cache_get(key)
    if raw:
        try:
            data = json.loads(raw)
            return data["columns"], data["rows"]
        except Exception:
            return None
    return None


def set_redshift_result(sql: str, columns: list[str], rows: list[list], ttl: int = 14400) -> None:
    """Cache should be skipped for time-sensitive queries (caller's responsibility)."""
    key = redshift_cache_key(sql)
    cache_set(key, json.dumps({"columns": columns, "rows": rows}), ttl)


def get_filter_values(table_fqn: str, col_name: str) -> list[str] | None:
    key = filter_vals_cache_key(table_fqn, col_name)
    raw = cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def set_filter_values(table_fqn: str, col_name: str, values: list[str], ttl: int = 86400) -> None:
    key = filter_vals_cache_key(table_fqn, col_name)
    cache_set(key, json.dumps(values), ttl)


def get_session_summary(thread_id: str) -> str | None:
    key = session_summary_key(thread_id)
    return cache_get(key)


def set_session_summary(thread_id: str, summary: str, ttl: int = 1800) -> None:
    key = session_summary_key(thread_id)
    cache_set(key, summary, ttl)


def get_json(key: str) -> object | None:
    """Return a deserialized JSON value or None on miss/error."""
    raw = cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def set_json(key: str, value: object, ttl: int = 86400) -> None:
    """Serialize value as JSON and cache it."""
    try:
        cache_set(key, json.dumps(value), ttl)
    except Exception as e:
        logger.warning("redis set_json failed | key={} | error={}", key[:60], e)

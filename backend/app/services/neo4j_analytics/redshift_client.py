"""Async Redshift client for the analytics pipeline.

Uses redshift_connector with asyncio.to_thread for non-blocking execution.
All queries use parameterized execution — never string interpolation.
"""

from __future__ import annotations

import asyncio
import time

from app.core.config import settings
from app.core.logger import logger

_pool = None


async def init_redshift() -> None:
    """Initialize the Redshift connection pool."""
    global _pool
    try:
        import redshift_connector
        _pool = redshift_connector.connect(
            host=settings.REDSHIFT_HOST,
            database=settings.REDSHIFT_DB,
            user=settings.REDSHIFT_USER,
            password=settings.REDSHIFT_PASSWORD,
            port=getattr(settings, "REDSHIFT_PORT", 5439),
        )
        logger.info("Redshift connection initialized | host={}", settings.REDSHIFT_HOST)
    except Exception as e:
        logger.error("Redshift initialization failed: {}", e)
        raise


async def close_redshift() -> None:
    global _pool
    if _pool:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
        logger.info("Redshift connection closed")


def _get_connection():
    if not _pool:
        raise RuntimeError("Redshift not initialized — call init_redshift() first.")
    return _pool


def _execute_sync(sql: str, params: list | None = None, timeout_s: int = 30) -> tuple[list[str], list[list]]:
    """Synchronous execution — run via asyncio.to_thread from async callers."""
    conn = _get_connection()
    cursor = conn.cursor()
    try:
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return columns, [list(r) for r in rows]
    finally:
        cursor.close()


async def execute_query(
    sql: str,
    params: list | None = None,
    timeout_s: int = 30,
    thread_id: str = "",
) -> tuple[list[str], list[list]]:
    """Execute a parameterized SQL query on Redshift.

    Returns (columns, rows). All values in rows are Python primitives.
    Raises on execution error — caller decides how to handle.
    """
    t0 = time.monotonic()
    try:
        columns, rows = await asyncio.wait_for(
            asyncio.to_thread(_execute_sync, sql, params, timeout_s),
            timeout=timeout_s + 5,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("redshift | thread={} | ms={:.0f} | rows={}", thread_id, elapsed_ms, len(rows))
        return columns, rows
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.warning("redshift timeout | thread={} | ms={:.0f}", thread_id, elapsed_ms)
        raise TimeoutError(f"Redshift query timed out after {timeout_s}s")
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("redshift error | thread={} | ms={:.0f} | error={}", thread_id, elapsed_ms, e)
        raise

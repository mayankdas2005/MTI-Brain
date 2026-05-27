"""Async Redshift client for the analytics pipeline.

Uses redshift_connector with a queue.Queue-based connection pool (size 3)
so concurrent queries from decomposed sub-queries don't conflict.
All queries use parameterized execution — never string interpolation.
"""

from __future__ import annotations

import asyncio
import queue
import time

from app.core.config import settings
from app.core.logger import logger

_POOL_SIZE = 3
_connection_pool: queue.Queue | None = None


def _make_connection():
    import redshift_connector
    return redshift_connector.connect(
        host=settings.REDSHIFT_HOST,
        database=settings.REDSHIFT_DB,
        user=settings.REDSHIFT_USER,
        password=settings.REDSHIFT_PASSWORD,
        port=getattr(settings, "REDSHIFT_PORT", 5439),
    )


async def init_redshift() -> None:
    """Initialize the Redshift connection pool (3 connections)."""
    global _connection_pool
    try:
        pool: queue.Queue = queue.Queue(maxsize=_POOL_SIZE)
        for _ in range(_POOL_SIZE):
            conn = await asyncio.to_thread(_make_connection)
            pool.put_nowait(conn)
        _connection_pool = pool
        logger.info("Redshift pool initialized | size={} | host={}", _POOL_SIZE, settings.REDSHIFT_HOST)
    except Exception as e:
        logger.error("Redshift initialization failed: {}", e)
        raise


async def close_redshift() -> None:
    global _connection_pool
    pool, _connection_pool = _connection_pool, None
    if pool:
        while not pool.empty():
            try:
                conn = pool.get_nowait()
                conn.close()
            except Exception:
                pass
        logger.info("Redshift pool closed")


def _get_pool() -> queue.Queue:
    if not _connection_pool:
        raise RuntimeError("Redshift not initialized — call init_redshift() first.")
    return _connection_pool


def _run_cursor(conn, sql: str, params: list | None) -> tuple[list[str], list[list]]:
    """Execute SQL on a connection and return (columns, rows)."""
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


def _execute_sync(sql: str, params: list | None = None, timeout_s: int = 30) -> tuple[list[str], list[list]]:
    """Borrow a connection from the pool, execute, return it. Runs in a thread.

    On BrokenPipe / stale connection: reconnects once and retries the query
    before raising so the caller doesn't burn a repair slot on a network glitch.
    """
    pool = _get_pool()
    try:
        conn = pool.get(timeout=timeout_s)
    except queue.Empty:
        raise TimeoutError("All Redshift connections busy — pool exhausted.")

    logger.info("redshift | SQL preview | {}", sql[:400])

    try:
        columns, rows = _run_cursor(conn, sql, params)
        logger.info("redshift | query OK | rows={} | columns={}", len(rows), columns)
        return columns, rows
    except Exception as first_err:
        logger.warning("redshift | query failed, reconnecting | error={}", first_err)
        try:
            conn.close()
            conn = _make_connection()
            columns, rows = _run_cursor(conn, sql, params)
            logger.info("redshift | query OK after reconnect | rows={} | columns={}", len(rows), columns)
            return columns, rows
        except Exception as retry_err:
            logger.error("redshift | reconnect+retry failed | error={}", retry_err)
            raise retry_err
    finally:
        try:
            pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass


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
        logger.info("redshift | DONE | thread={} | ms={:.0f} | rows={} | columns={}", thread_id, elapsed_ms, len(rows), columns)
        return columns, rows
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.warning("redshift timeout | thread={} | ms={:.0f}", thread_id, elapsed_ms)
        raise TimeoutError(f"Redshift query timed out after {timeout_s}s")
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("redshift error | thread={} | ms={:.0f} | error={}", thread_id, elapsed_ms, e)
        raise

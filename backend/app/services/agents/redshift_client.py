"""Async Redshift client for the analytics pipeline.

Uses redshift_connector with a queue.Queue-based connection pool (size 6)
so concurrent probe queries (context_fetcher) and actual query execution
don't compete for connections.
All queries use parameterized execution — never string interpolation.
"""

from __future__ import annotations

import asyncio
import queue
import time

from app.core.config import settings
from app.core.logger import logger
from app.core.retry import is_transient

_POOL_SIZE = 6
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
    """Initialize the Redshift connection pool (6 connections)."""
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


def _execute_sync(sql: str, params: list | None = None, timeout_s: int = 60) -> tuple[list[str], list[list]]:
    """Borrow a connection from the pool, execute, return it. Runs in a thread.

    On timeout / stale connection / broken pipe: reconnects and retries up to
    3 attempts total (1 original + 2 retries) before raising.
    """
    pool = _get_pool()
    try:
        conn = pool.get(timeout=timeout_s)
    except queue.Empty:
        raise TimeoutError("All Redshift connections busy — pool exhausted.")

    logger.info("redshift | SQL preview | {}", sql)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            columns, rows = _run_cursor(conn, sql, params)
            logger.info("redshift | query OK | attempt={} | rows={} | columns={}", attempt + 1, len(rows), columns)
            break
        except Exception as exc:
            last_err = exc
            if not is_transient(exc):
                logger.error("redshift | non-transient error | error={}", exc)
                raise
            if attempt < 2:
                delay = 0.5 * (attempt + 1)
                logger.warning("redshift | transient error attempt {}/3 — reconnecting in {:.1f}s | error={}", attempt + 1, delay, exc)
                time.sleep(delay)
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn = _make_connection()
                except Exception as conn_err:
                    logger.error("redshift | reconnect failed | error={}", conn_err)
                    raise conn_err
            else:
                logger.error("redshift | all 3 attempts failed | error={}", exc)
                raise
    else:
        raise last_err  # type: ignore[misc]

    try:
        try:
            conn.rollback()
        except Exception:
            pass
        pool.put_nowait(conn)
    except queue.Full:
        try:
            conn.close()
        except Exception:
            pass

    return columns, rows


async def get_table_columns(schema: str, table: str, col_names: list[str]) -> list[list]:
    """Return [[col_name, data_type], ...] for the requested columns via information_schema.

    Only returns rows for columns that actually exist in Redshift — caller can detect
    missing columns (e.g. base_currency not in Neo4j) by comparing to the input list.
    Uses individual %s placeholders — redshift_connector does not support list/array params.
    """
    if not col_names:
        return []
    placeholders = ", ".join(["%s"] * len(col_names))
    sql = (
        "SELECT column_name, data_type "
        "FROM information_schema.columns "
        f"WHERE table_schema = %s AND table_name = %s AND column_name IN ({placeholders})"
    )
    params = [schema, table] + list(col_names)
    try:
        _, rows = await asyncio.to_thread(
            _execute_sync, sql, params, 15
        )
        return [list(r) for r in rows]
    except Exception as e:
        logger.warning("redshift | get_table_columns failed | {}.{} | error={}", schema, table, e)
        return []


async def execute_query(
    sql: str,
    params: list | None = None,
    timeout_s: int = 60,
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

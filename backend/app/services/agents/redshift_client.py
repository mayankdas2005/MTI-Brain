"""Async Redshift client for the analytics pipeline.

Uses psycopg3 (async) with psycopg_pool.AsyncConnectionPool so queries are
truly non-blocking — no thread-pool contention under burst load.

Two pools are maintained:
  - _admin_pool: full privileges (REDSHIFT_USER) — for admin-role users
  - _readonly_pool: SELECT-only (REDSHIFT_READONLY_USER) — for regular users
If REDSHIFT_READONLY_USER is not configured, falls back to the admin pool.
"""

from __future__ import annotations

import asyncio
import time

import psycopg
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logger import logger
from app.core.retry import is_transient

_POOL_MIN = 2
_POOL_MAX = 10

_admin_pool: AsyncConnectionPool | None = None
_readonly_pool: AsyncConnectionPool | None = None


def _conninfo(user: str | None = None, password: str | None = None, dbname: str | None = None) -> str:
    return psycopg.conninfo.make_conninfo(
        host=settings.REDSHIFT_HOST,
        port=getattr(settings, "REDSHIFT_PORT", 5439),
        dbname=dbname or settings.REDSHIFT_DB,
        user=user or settings.REDSHIFT_USER,
        password=password or settings.REDSHIFT_PASSWORD,
        sslmode=settings.REDSHIFT_SSL_MODE,
    )


_CONNECT_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 10,
    "keepalives_count": 5,
    "connect_timeout": 10,
    "client_encoding": "utf8",
    "row_factory": tuple_row,
}


async def init_redshift() -> None:
    """Initialize the Redshift connection pools — admin and readonly."""
    global _admin_pool, _readonly_pool

    admin_conninfo = _conninfo()
    _admin_pool = AsyncConnectionPool(
        conninfo=admin_conninfo,
        min_size=_POOL_MIN,
        max_size=_POOL_MAX,
        timeout=10,
        max_idle=300,
        kwargs=_CONNECT_KWARGS,
        open=False,
    )
    await _admin_pool.open(wait=True, timeout=15)
    logger.info("Redshift admin pool ready | min={} max={} | host={}", _POOL_MIN, _POOL_MAX, settings.REDSHIFT_HOST)

    if settings.REDSHIFT_READONLY_USER:
        ro_conninfo = _conninfo(
            user=settings.REDSHIFT_READONLY_USER,
            password=settings.REDSHIFT_READONLY_PASSWORD,
        )
        _readonly_pool = AsyncConnectionPool(
            conninfo=ro_conninfo,
            min_size=_POOL_MIN,
            max_size=_POOL_MAX,
            timeout=10,
            max_idle=300,
            kwargs=_CONNECT_KWARGS,
            open=False,
        )
        await _readonly_pool.open(wait=True, timeout=15)
        logger.info("Redshift readonly pool ready | min={} max={}", _POOL_MIN, _POOL_MAX)
    else:
        logger.warning("Redshift REDSHIFT_READONLY_USER not configured — all queries use admin pool")
        _readonly_pool = _admin_pool


async def redshift_keepalive(interval_s: int = 60) -> None:
    """Ping Redshift every interval_s seconds to prevent Serverless auto-suspend."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            pool = _admin_pool
            if not pool:
                continue
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
            logger.debug("redshift | keepalive OK")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("redshift | keepalive failed | error={}", exc)


async def close_redshift() -> None:
    global _admin_pool, _readonly_pool
    pools_to_close: list[AsyncConnectionPool] = []
    seen_ids: set[int] = set()
    for p in [_admin_pool, _readonly_pool]:
        if p and id(p) not in seen_ids:
            seen_ids.add(id(p))
            pools_to_close.append(p)
    _admin_pool = _readonly_pool = None
    for pool in pools_to_close:
        await pool.close()
    logger.info("Redshift pools closed")


def _get_pool(readonly: bool = False) -> AsyncConnectionPool:
    pool = _readonly_pool if readonly else _admin_pool
    if not pool:
        raise RuntimeError("Redshift not initialized — call init_redshift() first.")
    return pool


async def _execute(
    sql: str,
    params: list | None = None,
    timeout_s: int = 60,
    quiet: bool = False,
    readonly: bool = False,
) -> tuple[list[str], list[list]]:
    """Borrow a connection from the pool, execute, return it.

    On transient errors: retries up to 3 times with exponential backoff.
    psycopg_pool automatically discards broken connections.
    """
    pool = _get_pool(readonly=readonly)

    if not quiet:
        logger.info("redshift | SQL preview | {}", sql[:200])

    last_err: Exception | None = None

    for attempt in range(3):
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    f"SET statement_timeout = {int(timeout_s) * 1000}"
                )
                cursor = await conn.execute(sql, params)
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = await cursor.fetchall()
                    result = columns, [list(r) for r in rows]
                else:
                    result = [], []
                if not quiet:
                    logger.info(
                        "redshift | query OK | attempt={} | rows={} | columns={}",
                        attempt + 1, len(result[1]), result[0],
                    )
                return result
        except Exception as exc:
            last_err = exc
            if not is_transient(exc):
                logger.error("redshift | non-transient error | error={}", exc)
                raise
            if attempt < 2:
                delay = 0.5 * (attempt + 1)
                logger.warning(
                    "redshift | transient error attempt {}/3 — retrying in {:.1f}s | error={}",
                    attempt + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("redshift | all 3 attempts failed | error={}", exc)
                raise

    raise last_err  # type: ignore[misc]


async def fetch_table_schema(schema: str, table: str) -> list[list]:
    """Return [[col_name, data_type], ...] for ALL columns in the table.

    Caches via Redis (TTL 1 day).
    """
    from app.services.agents.redis_client import get_schema_cols, set_schema_cols

    cached = get_schema_cols(schema, table)
    if cached is not None:
        return cached

    logger.info("schema_cols | CACHE MISS | {}.{} | fetching full schema from information_schema", schema, table)
    sql = (
        "SELECT column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position"
    )
    try:
        _, rows = await _execute(sql, [schema, table], 15, quiet=True)
        all_cols = [list(r) for r in rows]
        set_schema_cols(schema, table, all_cols)
        logger.info("schema_cols | FETCHED | {}.{} | total_cols={}", schema, table, len(all_cols))
        return all_cols
    except Exception as e:
        logger.warning("redshift | fetch_table_schema failed | {}.{} | error={}", schema, table, e)
        return []


async def get_table_columns(schema: str, table: str, col_names: list[str]) -> list[list]:
    """Return [[col_name, data_type], ...] for the requested columns.

    Results cached in Redis for 1 day per table.
    """
    from app.services.agents.redis_client import get_schema_cols

    if not col_names:
        return []

    cached = get_schema_cols(schema, table)
    if cached is not None:
        requested = set(col_names)
        matched = [row for row in cached if row[0] in requested]
        logger.info("schema_cols | CACHE HIT | {}.{} | total_cols={} | requested={} | found={}", schema, table, len(cached), len(col_names), len(matched))
        return matched

    all_cols = await fetch_table_schema(schema, table)
    requested = set(col_names)
    matched = [row for row in all_cols if row[0] in requested]
    logger.info("schema_cols | CACHED | {}.{} | total_cols={} | requested={} | found={}", schema, table, len(all_cols), len(col_names), len(matched))
    return matched


async def execute_query(
    sql: str,
    params: list | None = None,
    timeout_s: int = 60,
    thread_id: str = "",
    readonly: bool = True,
) -> tuple[list[str], list[list]]:
    """Execute a parameterized SQL query on Redshift.

    Returns (columns, rows). All values in rows are Python primitives.
    readonly=True (default) uses the SELECT-only pool for defense-in-depth.
    """
    t0 = time.monotonic()
    try:
        columns, rows = await asyncio.wait_for(
            _execute(sql, params, timeout_s, False, readonly),
            timeout=timeout_s + 5,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("redshift | DONE | thread={} | ms={:.0f} | rows={} | columns={}", thread_id, elapsed_ms, len(rows), columns)
        rows = _normalize_unrealistic_numbers(rows)
        return columns, rows
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.warning("redshift timeout | thread={} | ms={:.0f}", thread_id, elapsed_ms)
        raise TimeoutError(f"Redshift query timed out after {timeout_s}s")
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("redshift error | thread={} | ms={:.0f} | error={}", thread_id, elapsed_ms, e)
        raise


def _normalize_unrealistic_numbers(rows: list[list]) -> list[list]:
    """Scale down numeric values >= 1 billion to hundreds-of-millions range.

    Mocked source data produces unrealistic aggregations (trillions). This
    brings any value with 10+ digits down to a 9-figure range while preserving
    sign and leading significant digits.
    """
    import decimal
    import math

    THRESHOLD = 1_000_000_000

    normalized = []
    for row in rows:
        new_row = []
        for v in row:
            if isinstance(v, (int, float, decimal.Decimal)):
                num = float(v)
                if abs(num) >= THRESHOLD:
                    sign = -1 if num < 0 else 1
                    abs_v = abs(num)
                    exponent = math.floor(math.log10(abs_v))
                    scale = 10 ** (exponent - 8)
                    scaled = abs_v / scale
                    new_row.append(round(sign * scaled, 2))
                else:
                    new_row.append(v)
            else:
                new_row.append(v)
        normalized.append(new_row)
    return normalized


async def fetch_table_distkeys(schema: str, table: str) -> dict[str, bool]:
    """Return {column_name: is_distkey} from pg_table_def.

    Empty dict on any failure.
    """
    sql = (
        "SELECT \"column\", distkey "
        "FROM pg_table_def "
        "WHERE schemaname = %s AND tablename = %s"
    )
    try:
        _, rows = await _execute(sql, [schema, table], 10, quiet=True)
        return {str(r[0]): bool(r[1]) for r in rows}
    except Exception as e:
        logger.debug("redshift | fetch_distkeys | {}.{} | unavailable | error={}", schema, table, e)
        return {}


async def run_explain(sql: str) -> list[str]:
    """Run EXPLAIN on a SQL string and return the plan lines.

    Returns empty list on any failure.
    """
    explain_sql = "EXPLAIN " + sql
    try:
        _, rows = await _execute(explain_sql, None, 15, quiet=True)
        return [str(row[0]) for row in rows]
    except Exception as e:
        logger.warning("redshift | run_explain failed | error={}", e)
        return []

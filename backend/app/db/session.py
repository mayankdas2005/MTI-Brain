"""Async database engine and session management.

Architecture note — PgBouncer transaction-mode compatibility
────────────────────────────────────────────────────────────
The application runs behind PgBouncer in TRANSACTION pooling mode.
This changes the rules for how SQLAlchemy must be configured:

  1. pool_pre_ping=False
     PgBouncer validates server connections itself. Enabling pre_ping makes
     SQLAlchemy send a "SELECT 1" on every checkout, which wastes a server
     connection slot through PgBouncer and adds a round-trip.

  2. pool_size kept small (default 2 per worker)
     SQLAlchemy's pool manages CLIENT→PgBouncer sockets, not Postgres server
     connections. PgBouncer does the real pooling. One or two warm sockets per
     worker is all that's needed; the rest is overhead.

  3. pool_recycle < PgBouncer SERVER_IDLE_TIMEOUT (600 s)
     If SQLAlchemy recycles AFTER PgBouncer has already closed the server-side
     connection, the next checkout gets a broken connection. We recycle at 500 s.

  4. Read engine uses isolation_level="AUTOCOMMIT"
     In transaction mode, an open BEGIN pins a PgBouncer server connection to
     the client for the entire session lifetime. With AUTOCOMMIT isolation, no
     BEGIN is ever sent — each SELECT acquires a server connection, runs, and
     releases it immediately, multiplying effective throughput under load.
     Note: autobegin=False on the Session factory is NOT the right approach —
     in SQLAlchemy 2.0 it means "require explicit session.begin()" which raises
     InvalidRequestError. AUTOCOMMIT at the connection level is the correct path.

  5. Write sessions use the default engine (with BEGIN/COMMIT)
     Multi-statement writes need transaction integrity. The write session
     explicitly commits or rolls back, which signals PgBouncer to release the
     server connection.

LangGraph checkpointer note
───────────────────────────
LangGraph's AsyncPostgresSaver must not share this engine. Create a dedicated
asyncpg pool (see ``get_langgraph_pool`` below) with:
  - prepared_statement_cache_size=0  (prepared statements not supported in
                                      PgBouncer transaction mode)
  - statement_cache_size=0
The pool should connect directly to PgBouncer just like the SQLAlchemy engine.
"""

import ssl
from collections.abc import AsyncGenerator
from pathlib import Path

from app.core.config import settings
from app.core.logger import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def build_ssl_context() -> ssl.SSLContext | bool:
    ssl_mode = settings.DATABASE_SSL_MODE
    if ssl_mode == "disable":
        return False

    if ssl_mode in {"verify-ca", "verify-full"} and not settings.DATABASE_SSL_ROOT_CERT:
        raise ValueError(
            "DATABASE_SSL_ROOT_CERT is required when DATABASE_SSL_MODE is verify-ca or verify-full"
        )

    if settings.DATABASE_SSL_ROOT_CERT:
        cert_path = Path(settings.DATABASE_SSL_ROOT_CERT)
        if not cert_path.exists():
            raise FileNotFoundError(f"SSL root certificate not found: {cert_path}")
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.load_verify_locations(cafile=str(cert_path))
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        ssl_ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    else:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    if ssl_mode == "verify-full":
        ssl_ctx.check_hostname = True

    return ssl_ctx


_ssl = build_ssl_context()
connect_args = {"ssl": _ssl} if _ssl else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    # Pre-ping validates each connection before use (~1 ms overhead per checkout).
    # Required to detect connections that were silently dropped by PgBouncer or
    # the network — without it, write sessions receive a dead asyncpg connection
    # and fail with ConnectionDoesNotExistError mid-query.
    # The ping is a single autocommit SELECT 1; in PgBouncer transaction mode it
    # acquires a server connection, runs the ping, and releases it immediately —
    # no server connection is held after the ping completes.
    pool_pre_ping=True,
    # Keep the SQLAlchemy pool small — it caches client→PgBouncer sockets,
    # not actual Postgres server connections. PgBouncer handles the real pool.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    # Recycle BEFORE PgBouncer's SERVER_IDLE_TIMEOUT (default 600 s) so we
    # never hand out a socket whose server-side connection PgBouncer has closed.
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args=connect_args,
    echo=settings.DEBUG,
)

# Write session factory — standard engine with autobegin=True (default).
# Multi-statement operations run inside an explicit transaction; PgBouncer
# holds one server connection for the request and releases it on COMMIT/ROLLBACK.
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Autocommit sub-engine for read sessions.
# execution_options(isolation_level="AUTOCOMMIT") tells asyncpg to skip
# BEGIN/COMMIT entirely — each statement gets its own implicit server-side
# transaction. PgBouncer receives a plain SELECT, routes it to a free server
# connection, and reclaims that connection the moment the query finishes.
# This is the correct way to achieve per-statement connection release in
# SQLAlchemy 2.0; autobegin=False on the Session raises InvalidRequestError.
_read_engine = engine.execution_options(isolation_level="AUTOCOMMIT")

async_read_session_factory = async_sessionmaker(
    _read_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def warm_pool() -> None:
    """Verify DB reachability at startup and pre-open the minimum pool sockets.

    With PgBouncer in transaction mode, warming SQLAlchemy connections only
    pre-establishes the cheap CLIENT→PgBouncer TCP sockets, not the expensive
    server connections (PgBouncer manages those). We warm pool_size sockets so
    the first burst of requests avoids paying TCP-connect latency to PgBouncer.
    """
    import asyncio

    target = min(settings.DB_POOL_SIZE, 4)

    async def _open_one() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.gather(*[_open_one() for _ in range(target)])
        logger.info(
            f"DB pool ready — {target} client→PgBouncer sockets pre-opened"
        )
    except Exception as exc:
        logger.warning(f"DB pool warm-up failed (non-fatal — pool will connect on first request): {exc}")


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Write session — full transaction, explicit COMMIT/ROLLBACK.

    Use for INSERT, UPDATE, DELETE operations. PgBouncer holds one server
    connection for the duration of the transaction and releases it on COMMIT.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """Read session — AUTOCOMMIT isolation, no BEGIN/COMMIT overhead.

    Each SELECT runs directly against a PgBouncer server connection which is
    released as soon as the statement completes. 20 concurrent reads need only
    as many live server connections as are actively executing — not one per
    open HTTP request.
    """
    async with async_read_session_factory() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose the async engine and close all pooled connections."""
    logger.info("Disposing database engine")
    await engine.dispose()


def get_langgraph_dsn() -> str:
    """Return a plain asyncpg DSN for use with LangGraph's AsyncPostgresSaver.

    LangGraph's checkpointer must NOT share the SQLAlchemy engine. It needs its
    own asyncpg pool configured for PgBouncer transaction-mode compatibility:
      - prepared_statement_cache_size=0  (prepared stmts break in txn mode)
      - statement_cache_size=0

    Usage in your agent setup::

        import asyncpg
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = await asyncpg.create_pool(
            get_langgraph_dsn(),
            min_size=2,
            max_size=5,
            init=_disable_prepared_stmts,  # see below
        )

        async def _disable_prepared_stmts(conn):
            await conn.execute("SET plan_cache_mode = force_generic_plan")
            conn._protocol.queries_count = 0  # reset asyncpg's cache counter

        checkpointer = AsyncPostgresSaver(pool)
    """
    from urllib.parse import quote_plus
    pw = quote_plus(settings.POSTGRES_PASSWORD)
    return (
        f"postgresql://{settings.POSTGRES_USER}:{pw}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )

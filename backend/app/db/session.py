"""Async database engine and session management.

Configures the SQLAlchemy async engine, session factories, connection-pool
warming, and dependency-injectable session generators for read and write
workloads.
"""

import ssl
from collections.abc import AsyncGenerator
from pathlib import Path

from app.core.config import settings
from app.core.logger import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine


def build_ssl_context() -> ssl.SSLContext | bool:
    """Build an SSL context for the database connection.

    Reads ``DATABASE_SSL_MODE`` and ``DATABASE_SSL_ROOT_CERT`` from
    application settings to construct the appropriate SSL context.

    Returns:
        An ``ssl.SSLContext`` configured for the requested SSL mode, or
        ``False`` when SSL is disabled.

    Raises:
        ValueError: If SSL mode is ``verify-ca`` or ``verify-full`` but
            ``DATABASE_SSL_ROOT_CERT`` is not set.
        FileNotFoundError: If the specified SSL root certificate file does
            not exist on disk.
    """
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
    # pool_pre_ping validates each connection on checkout (~1ms overhead).
    # Combined with the longer pool_recycle from config (default 1800s), this
    # eliminates the ~1.3s reconnect cost that occurred every 60s under the
    # previous hardcoded recycle value.
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args=connect_args,
    echo=settings.DEBUG,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def warm_pool() -> None:
    """Pre-create pool connections so early requests avoid cold-connect latency.

    Opens up to 3 connections (or ``DB_POOL_SIZE``, whichever is smaller) in
    parallel so that the first burst of concurrent requests does not each pay
    the ~1.3 s cold-connect penalty.
    """
    import asyncio

    async def _open_one():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    # Warm up 3 connections in parallel
    await asyncio.gather(*[_open_one() for _ in range(min(3, settings.DB_POOL_SIZE))])
    logger.info(f"Connection pool warmed ({min(3, settings.DB_POOL_SIZE)} connections)")


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional session for write endpoints.

    Intended for create, update, and delete operations. The session is
    automatically committed on success and rolled back on failure.

    Yields:
        An ``AsyncSession`` bound to the default engine.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a read-only session for query endpoints.

    Used by get, list, and search operations. The session intentionally
    skips ``COMMIT`` to save one database round-trip (~500 ms on
    high-latency connections).

    Yields:
        An ``AsyncSession`` bound to the default engine.
    """
    async with async_session_factory() as session:
        yield session


async def dispose_engine():
    """Dispose the async engine and close all pooled connections.

    Should be called during application shutdown to release database
    resources gracefully.
    """
    logger.info("Disposing database engine")
    await engine.dispose()

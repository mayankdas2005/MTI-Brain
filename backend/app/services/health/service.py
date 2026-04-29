"""Health check functions for downstream service dependencies."""

import pybreaker
from app.core.circuit_breaker import postgres_breaker
from app.core.logger import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_postgres(db: AsyncSession) -> dict:
    try:

        @postgres_breaker
        async def _check():
            await db.execute(text("SELECT 1"))

        await _check()
        return {"status": "ok"}

    except pybreaker.CircuitBreakerError:
        logger.warning("Postgres circuit breaker is OPEN - skipping check")
        return {"status": "circuit_open"}

    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        return {"status": "down", "error": str(e)}

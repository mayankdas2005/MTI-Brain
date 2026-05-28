"""Health check endpoint for readiness probes."""

import asyncio
from datetime import datetime, timezone

import pybreaker
from app.core.circuit_breaker import embedding_breaker, llm_breaker
from app.db.session import get_read_session
from app.services.health import check_neo4j, check_postgres, check_redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _breaker_status(breaker: pybreaker.CircuitBreaker) -> dict:
    state = breaker.current_state
    result = {"status": "ok" if state == "closed" else "circuit_open", "state": state}
    if state == "open":
        result["fail_count"] = breaker.fail_counter
    return result


@router.get("/health", summary="Readiness - can it serve traffic?")
async def readiness(db: AsyncSession = Depends(get_read_session)):
    pg, neo4j, redis = await asyncio.gather(
        check_postgres(db),
        check_neo4j(),
        check_redis(),
    )

    services = {
        "postgres": pg,
        "neo4j": neo4j,
        "redis": redis,
        "llm": _breaker_status(llm_breaker),
        "embeddings": _breaker_status(embedding_breaker),
    }

    if any(s["status"] == "down" for s in services.values()):
        overall = "unhealthy"
        status_code = 503
    elif any(s["status"] not in ("ok", "disabled") for s in services.values()):
        overall = "degraded"
        status_code = 200
    else:
        overall = "healthy"
        status_code = 200

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "services": services,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

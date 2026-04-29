"""Health check endpoint for readiness probes."""

from datetime import datetime, timezone

from app.db.session import get_read_session
from app.services.health import check_postgres
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/health", summary="Readiness - can it serve traffic?")
async def readiness(db: AsyncSession = Depends(get_read_session)):
    pg = await check_postgres(db)

    services = {"postgres": pg}

    if pg["status"] == "down":
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

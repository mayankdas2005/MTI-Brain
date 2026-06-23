"""FastAPI application entry point with lifespan management."""

import asyncio
import sys
from app.core.langfuse_integration import init_langfuse, shutdown_langfuse
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.api.v1 import v1_router
from app.core.config import settings
from app.core.logger import logger
from app.core.middleware import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
)
from app.core.rate_limit import limiter
from app.db import dispose_engine, warm_pool
from app.services.agents.graph import init_analytics_pipeline, shutdown_analytics_pipeline
from app.services.agents.redshift_client import redshift_keepalive
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} [env={settings.ENVIRONMENT}]")
    init_langfuse()
    asyncio.create_task(warm_pool())
    _keepalive_task = None
    try:
        await init_analytics_pipeline()
        _keepalive_task = asyncio.create_task(redshift_keepalive(), name="redshift-keepalive")
        logger.info("Redshift keepalive started | interval=30s")
    except Exception as e:
        logger.error(f"Analytics pipeline init failed (continuing without it): {e}")
    try:
        yield
    finally:
        logger.info("Shutting down")
        if _keepalive_task:
            _keepalive_task.cancel()
            try:
                await _keepalive_task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(shutdown_analytics_pipeline(), timeout=4.0)
        except Exception:
            logger.warning("Analytics pipeline shutdown timed out or failed")
        shutdown_langfuse()
        try:
            await asyncio.wait_for(dispose_engine(), timeout=1.0)
        except Exception:
            pass
        logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME, lifespan=lifespan, description="MTI Brain Backend API"
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Filename"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.include_router(health_router, tags=["health"])
app.include_router(v1_router, prefix="/api/v1")

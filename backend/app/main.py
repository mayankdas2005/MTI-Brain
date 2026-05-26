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
from app.services.neo4j_analytics.graph import init_analytics_pipeline, shutdown_analytics_pipeline
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
    await warm_pool()
    try:
        await init_analytics_pipeline()
    except Exception as e:
        logger.error(f"Analytics pipeline init failed (continuing without it): {e}")
    try:
        yield
    finally:
        logger.info("Shutting down")
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

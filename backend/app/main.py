"""FastAPI application entry point with lifespan management."""

import sys
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.api.v1 import v1_router
from app.core.config import settings
from app.core.logger import logger
from app.core.middleware import RequestIDMiddleware, TimingMiddleware
from app.db import dispose_engine, warm_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} [env={settings.ENVIRONMENT}]")
    await warm_pool()
    yield
    logger.info("Shutting down - releasing resources")
    await dispose_engine()


app = FastAPI(
    title=settings.APP_NAME, lifespan=lifespan, description="MTI Brain Backend API"
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.include_router(health_router, tags=["health"])
app.include_router(v1_router, prefix="/api/v1")

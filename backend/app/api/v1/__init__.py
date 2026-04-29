"""V1 API router aggregating auth, chat, and project endpoints."""

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.project import router as project_router
from fastapi import APIRouter

v1_router = APIRouter()
v1_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_router.include_router(chat_router, prefix="/chat", tags=["chat"])
v1_router.include_router(project_router, prefix="/projects", tags=["projects"])

"""V1 API router aggregating all endpoints."""

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.labels import router as labels_router
from app.api.v1.pinned_metrics import router as pinned_metrics_router
from app.api.v1.playbook import router as playbook_router
from app.api.v1.project import router as project_router
from fastapi import APIRouter

v1_router = APIRouter()
v1_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_router.include_router(chat_router, prefix="/chat", tags=["chat"])
v1_router.include_router(project_router, prefix="/projects", tags=["projects"])
v1_router.include_router(playbook_router, prefix="/playbook", tags=["playbook"])
v1_router.include_router(pinned_metrics_router, prefix="/pinned-metrics", tags=["pinned-metrics"])
v1_router.include_router(labels_router, prefix="/labels", tags=["labels"])
v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])

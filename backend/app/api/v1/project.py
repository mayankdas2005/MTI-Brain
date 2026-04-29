"""Project API endpoints for CRUD operations."""

import uuid

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import get_async_session, get_read_session
from app.schemas.project import (
    CreateProjectRequest,
    DeleteProjectResponse,
    ProjectDetail,
    ProjectOut,
    ThreadBrief,
    UpdateProjectRequest,
)
from app.services import conversation as conv_service
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


# ─── GET /projects ───


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    search: str | None = Query(
        default=None, description="Search projects by name/description"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    projects = await conv_service.list_projects(
        db, search=search, user_id=current_user.id
    )
    return [ProjectOut(**p) for p in projects]


# ─── POST /projects/create ───


@router.post("/create", response_model=ProjectOut, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    project = await conv_service.create_project(
        db, name=body.name, description=body.description,
        user_id=current_user.id,
    )
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        starred=project.starred,
        thread_count=0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


# ─── GET /projects/{project_id} ───


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    project = await conv_service.get_project(
        db, project_id, user_id=current_user.id
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectDetail(
        id=project["id"],
        name=project["name"],
        description=project["description"],
        starred=project["starred"],
        threads=[
            ThreadBrief(
                id=t["id"],
                title=t["title"],
                starred=t["starred"],
                created_at=t["created_at"],
                updated_at=t["updated_at"],
            )
            for t in project["threads"]
        ],
        created_at=project["created_at"],
        updated_at=project["updated_at"],
    )


# ─── PUT /projects/{project_id} ───


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: UpdateProjectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    project = await conv_service.update_project(
        db, project_id, name=body.name, description=body.description,
        user_id=current_user.id,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectOut(**project, thread_count=0)


# ─── DELETE /projects/{project_id} ───


@router.delete("/{project_id}", response_model=DeleteProjectResponse)
async def delete_project(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await conv_service.delete_project(
        db, project_id, user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return DeleteProjectResponse(deleted=True, project_id=project_id)


# ─── PATCH /projects/{project_id}/star ───


@router.patch("/{project_id}/star")
async def star_project(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    starred = await conv_service.star_project(
        db, project_id, user_id=current_user.id
    )
    if starred is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "starred": starred}

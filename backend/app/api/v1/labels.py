"""Thread Labels API — apply/remove colored labels on threads."""

import uuid

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import get_async_session, get_read_session
from app.schemas.user_features import ThreadLabelCreate, ThreadLabelOut
from app.services.user import labels as svc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("", response_model=list[ThreadLabelOut])
async def list_all_labels(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    """Return every label the user has applied across all threads (for filter UI)."""
    items = await svc.list_all_user_labels(db, user_id=current_user.id)
    return [ThreadLabelOut.model_validate(i) for i in items]


@router.get("/thread/{thread_id}", response_model=list[ThreadLabelOut])
async def list_thread_labels(
    thread_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    items = await svc.list_thread_labels(db, thread_id=thread_id, user_id=current_user.id)
    return [ThreadLabelOut.model_validate(i) for i in items]


@router.post("/thread/{thread_id}", response_model=ThreadLabelOut, status_code=201)
async def add_label(
    thread_id: uuid.UUID,
    body: ThreadLabelCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    item = await svc.add_thread_label(
        db,
        thread_id=thread_id,
        user_id=current_user.id,
        label=body.label,
        color=body.color,
    )
    await db.commit()
    return ThreadLabelOut.model_validate(item)


@router.delete("/{label_id}", status_code=204)
async def delete_label(
    label_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await svc.delete_thread_label(db, label_id=label_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Label not found")
    await db.commit()

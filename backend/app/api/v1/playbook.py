"""Playbook API — CRUD for user saved queries."""

import uuid

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import get_async_session, get_read_session
from app.schemas.user_features import SavedQueryCreate, SavedQueryOut, SavedQueryUpdate
from app.services.user import playbook as svc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("", response_model=list[SavedQueryOut])
async def list_playbook(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    items = await svc.list_saved_queries(db, user_id=current_user.id)
    return [SavedQueryOut.model_validate(i) for i in items]


@router.post("", response_model=SavedQueryOut, status_code=201)
async def create_playbook_entry(
    body: SavedQueryCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    item = await svc.create_saved_query(
        db, user_id=current_user.id, name=body.name, query_text=body.query_text
    )
    await db.commit()
    return SavedQueryOut.model_validate(item)


@router.patch("/{query_id}", response_model=SavedQueryOut)
async def update_playbook_entry(
    query_id: uuid.UUID,
    body: SavedQueryUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    item = await svc.update_saved_query(
        db, query_id=query_id, user_id=current_user.id,
        name=body.name, query_text=body.query_text,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Saved query not found")
    await db.commit()
    return SavedQueryOut.model_validate(item)


@router.delete("/{query_id}", status_code=204)
async def delete_playbook_entry(
    query_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await svc.delete_saved_query(db, query_id=query_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved query not found")
    await db.commit()

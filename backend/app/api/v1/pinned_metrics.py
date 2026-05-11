"""Pinned Metrics API — CRUD for user home-page metric cards."""

import uuid

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import get_async_session, get_read_session
from app.schemas.user_features import PinnedMetricCreate, PinnedMetricOut, PinnedMetricUpdate
from app.services import user_features as svc
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("", response_model=list[PinnedMetricOut])
async def list_pinned_metrics(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    items = await svc.list_pinned_metrics(db, user_id=current_user.id)
    return [PinnedMetricOut.model_validate(i) for i in items]


@router.post("", response_model=PinnedMetricOut, status_code=201)
async def create_pinned_metric(
    body: PinnedMetricCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    item = await svc.create_pinned_metric(
        db,
        user_id=current_user.id,
        label=body.label,
        source_query=body.source_query,
        position=body.position,
    )
    await db.commit()
    return PinnedMetricOut.model_validate(item)


@router.patch("/{metric_id}", response_model=PinnedMetricOut)
async def update_pinned_metric(
    metric_id: uuid.UUID,
    body: PinnedMetricUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    item = await svc.update_pinned_metric(
        db, metric_id=metric_id, user_id=current_user.id,
        label=body.label, position=body.position,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Pinned metric not found")
    await db.commit()
    return PinnedMetricOut.model_validate(item)


@router.delete("/{metric_id}", status_code=204)
async def delete_pinned_metric(
    metric_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await svc.delete_pinned_metric(db, metric_id=metric_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pinned metric not found")
    await db.commit()

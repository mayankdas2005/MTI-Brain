"""Pinned metrics service — home-page metric cards per user."""

import uuid

from app.core.logger import logger
from app.models.user_features import UserPinnedMetric
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def list_pinned_metrics(
    db: AsyncSession, user_id: uuid.UUID
) -> list[UserPinnedMetric]:
    result = await db.execute(
        select(UserPinnedMetric)
        .where(UserPinnedMetric.user_id == user_id)
        .order_by(UserPinnedMetric.position.asc(), UserPinnedMetric.created_at.asc())
    )
    return list(result.scalars().all())


async def create_pinned_metric(
    db: AsyncSession,
    user_id: uuid.UUID,
    label: str,
    source_query: str,
    position: int = 0,
) -> UserPinnedMetric:
    m = UserPinnedMetric(
        user_id=user_id, label=label, source_query=source_query, position=position
    )
    db.add(m)
    await db.flush()
    logger.info(f"Pinned metric created: {m.id} ({label}) for user {user_id}")
    return m


async def update_pinned_metric(
    db: AsyncSession,
    metric_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str | None = None,
    position: int | None = None,
) -> UserPinnedMetric | None:
    patch: dict = {}
    if label is not None:
        patch["label"] = label
    if position is not None:
        patch["position"] = position
    if patch:
        result = await db.scalars(
            update(UserPinnedMetric)
            .where(
                UserPinnedMetric.id == metric_id,
                UserPinnedMetric.user_id == user_id,
            )
            .values(**patch)
            .returning(UserPinnedMetric)
        )
        await db.flush()
        return result.one_or_none()
    result = await db.execute(
        select(UserPinnedMetric).where(
            UserPinnedMetric.id == metric_id, UserPinnedMetric.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def delete_pinned_metric(
    db: AsyncSession, metric_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    result = await db.execute(
        delete(UserPinnedMetric)
        .where(
            UserPinnedMetric.id == metric_id,
            UserPinnedMetric.user_id == user_id,
        )
        .returning(UserPinnedMetric.id)
    )
    await db.flush()
    return result.scalar_one_or_none() is not None

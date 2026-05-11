"""Service layer for Playbook, Pinned Metrics, and Thread Labels."""

import uuid

from app.core.logger import logger
from app.models.user_features import ThreadLabel, UserPinnedMetric, UserSavedQuery
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


# ─── Saved Queries (Playbook) ───

async def list_saved_queries(
    db: AsyncSession, user_id: uuid.UUID
) -> list[UserSavedQuery]:
    result = await db.execute(
        select(UserSavedQuery)
        .where(UserSavedQuery.user_id == user_id)
        .order_by(UserSavedQuery.created_at.asc())
    )
    return list(result.scalars().all())


async def create_saved_query(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    query_text: str,
) -> UserSavedQuery:
    q = UserSavedQuery(user_id=user_id, name=name, query_text=query_text)
    db.add(q)
    await db.flush()
    logger.info(f"Saved query created: {q.id} ({name}) for user {user_id}")
    return q


async def update_saved_query(
    db: AsyncSession,
    query_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str | None = None,
    query_text: str | None = None,
) -> UserSavedQuery | None:
    patch: dict = {}
    if name is not None:
        patch["name"] = name
    if query_text is not None:
        patch["query_text"] = query_text
    if not patch:
        result = await db.execute(
            select(UserSavedQuery).where(
                UserSavedQuery.id == query_id, UserSavedQuery.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    await db.execute(
        update(UserSavedQuery)
        .where(UserSavedQuery.id == query_id, UserSavedQuery.user_id == user_id)
        .values(**patch)
    )
    await db.flush()
    result = await db.execute(
        select(UserSavedQuery).where(UserSavedQuery.id == query_id)
    )
    return result.scalar_one_or_none()


async def delete_saved_query(
    db: AsyncSession, query_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(UserSavedQuery).where(
            UserSavedQuery.id == query_id, UserSavedQuery.user_id == user_id
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        return False
    await db.delete(obj)
    await db.flush()
    return True


# ─── Pinned Metrics ───

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
        await db.execute(
            update(UserPinnedMetric)
            .where(
                UserPinnedMetric.id == metric_id,
                UserPinnedMetric.user_id == user_id,
            )
            .values(**patch)
        )
        await db.flush()
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
        select(UserPinnedMetric).where(
            UserPinnedMetric.id == metric_id, UserPinnedMetric.user_id == user_id
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        return False
    await db.delete(obj)
    await db.flush()
    return True


# ─── Thread Labels ───

async def list_thread_labels(
    db: AsyncSession, thread_id: uuid.UUID, user_id: uuid.UUID
) -> list[ThreadLabel]:
    result = await db.execute(
        select(ThreadLabel).where(
            ThreadLabel.thread_id == thread_id, ThreadLabel.user_id == user_id
        )
    )
    return list(result.scalars().all())


async def list_all_user_labels(
    db: AsyncSession, user_id: uuid.UUID
) -> list[ThreadLabel]:
    """All labels the user has applied across all threads — used to populate filter UI."""
    result = await db.execute(
        select(ThreadLabel)
        .where(ThreadLabel.user_id == user_id)
        .order_by(ThreadLabel.created_at.desc())
    )
    return list(result.scalars().all())


async def add_thread_label(
    db: AsyncSession,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str,
    color: str = "blue",
) -> ThreadLabel:
    lbl = ThreadLabel(
        thread_id=thread_id, user_id=user_id, label=label, color=color
    )
    db.add(lbl)
    await db.flush()
    return lbl


async def delete_thread_label(
    db: AsyncSession,
    label_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(ThreadLabel).where(
            ThreadLabel.id == label_id, ThreadLabel.user_id == user_id
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        return False
    await db.delete(obj)
    await db.flush()
    return True

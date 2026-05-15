"""Thread labels service — coloured tags applied to conversation threads."""

import uuid

from app.models.user_features import ThreadLabel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


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
    """All labels the user has applied across all threads — for the filter UI."""
    result = await db.execute(
        select(ThreadLabel)
        .where(ThreadLabel.user_id == user_id)
        .order_by(ThreadLabel.created_at.desc())
        .limit(200)
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

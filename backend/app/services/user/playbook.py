"""Saved query (Playbook) service — user-curated reusable queries."""

import uuid

from app.core.logger import logger
from app.models.user_features import UserSavedQuery
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession


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

    result = await db.scalars(
        update(UserSavedQuery)
        .where(UserSavedQuery.id == query_id, UserSavedQuery.user_id == user_id)
        .values(**patch)
        .returning(UserSavedQuery)
    )
    await db.flush()
    return result.one_or_none()


async def delete_saved_query(
    db: AsyncSession, query_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    result = await db.execute(
        delete(UserSavedQuery)
        .where(UserSavedQuery.id == query_id, UserSavedQuery.user_id == user_id)
        .returning(UserSavedQuery.id)
    )
    await db.flush()
    return result.scalar_one_or_none() is not None

"""Feedback service - DB persistence for user feedback (no embedding)."""

import uuid

from app.core.logger import logger
from app.models.conversation import QuestFeedback
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def save_feedback(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    thread_id: uuid.UUID,
    liked: bool,
    comment: str | None = None,
) -> QuestFeedback:
    result = await db.execute(
        text(
            "SELECT id FROM quest_message "
            "WHERE conversation_id = :cid AND role = 'assistant' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    message_id = result.scalar_one_or_none()

    feedback = QuestFeedback(
        message_id=message_id,
        thread_id=thread_id,
        liked=liked,
        comment=comment,
        embedding=None,
    )
    db.add(feedback)
    await db.flush()
    logger.info(f"Feedback saved: conversation={conversation_id}, liked={liked}")
    return feedback


_FIND_THREAD_FEEDBACK_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        q.content AS question_text
    FROM quest_feedback f
    JOIN quest_message m ON m.id = f.message_id
    JOIN quest_message q ON q.conversation_id = m.conversation_id AND q.role = 'user'
    WHERE f.thread_id = :thread_id
      AND f.comment IS NOT NULL
      AND f.comment != ''
    ORDER BY f.created_at DESC
    LIMIT :limit
""")


async def find_thread_feedback(
    db: AsyncSession,
    thread_id: uuid.UUID,
    limit: int = 10,
) -> list[dict]:
    result = await db.execute(
        _FIND_THREAD_FEEDBACK_SQL,
        {"thread_id": str(thread_id), "limit": limit},
    )
    return [
        {
            "id": row.id,
            "liked": row.liked,
            "comment": row.comment,
            "thread_id": row.thread_id,
            "question_text": row.question_text[:200] if row.question_text else "",
            "source": "thread",
        }
        for row in result.fetchall()
    ]

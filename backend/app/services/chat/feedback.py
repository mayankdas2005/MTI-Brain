"""Feedback service — persistence and retrieval for user thumbs-up/down."""

from __future__ import annotations

import uuid

from app.core.logger import logger
from app.models.conversation import MTIBrainFeedback
from app.services.embeddings import embed_question
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def save_feedback(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    thread_id: uuid.UUID,
    liked: bool,
    comment: str | None = None,
) -> MTIBrainFeedback:
    """Save feedback and embed the question for future similarity search.

    Embedding is async and non-blocking. If it fails, feedback is still
    saved — just without an embedding (similarity search won't find it).
    """
    result = await db.execute(
        text(
            "SELECT id FROM mti_brain_message "
            "WHERE conversation_id = :cid AND role = 'assistant' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    message_id = result.scalar_one_or_none()

    # Embed the user's question so future similar questions can find this feedback
    question_result = await db.execute(
        text(
            "SELECT content FROM mti_brain_message "
            "WHERE conversation_id = :cid AND role = 'user' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    question_text = question_result.scalar_one_or_none() or ""
    embedding = await embed_question(question_text) if question_text else None

    feedback = MTIBrainFeedback(
        message_id=message_id,
        thread_id=thread_id,
        liked=liked,
        comment=comment,
        embedding=embedding,
    )
    db.add(feedback)
    await db.flush()
    logger.info(
        f"Feedback saved: conversation={conversation_id}, liked={liked}, "
        f"has_embedding={embedding is not None}"
    )
    return feedback


_FIND_THREAD_FEEDBACK_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        q.content AS question_text
    FROM mti_brain_feedback f
    LEFT JOIN mti_brain_message m ON m.id = f.message_id
    LEFT JOIN mti_brain_message q ON q.conversation_id = m.conversation_id AND q.role = 'user'
    WHERE f.thread_id = :thread_id
      AND f.comment IS NOT NULL
      AND f.comment != ''
    ORDER BY f.created_at DESC
    LIMIT :limit
""")


async def find_thread_feedback(
    db: AsyncSession,
    thread_id: uuid.UUID,
    limit: int = 5,
) -> list[dict]:
    """Get all feedback with comments from the current thread."""
    result = await db.execute(
        _FIND_THREAD_FEEDBACK_SQL,
        {"thread_id": str(thread_id), "limit": limit},
    )
    return [
        {
            "id": str(row.id),
            "liked": row.liked,
            "comment": row.comment,
            "thread_id": str(row.thread_id),
            "question_text": (row.question_text or "")[:200],
            "source": "thread",
        }
        for row in result.fetchall()
    ]


_FIND_SIMILAR_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        q.content AS question_text,
        1 - (f.embedding <=> CAST(:embedding AS vector)) AS similarity
    FROM mti_brain_feedback f
    LEFT JOIN mti_brain_message m ON m.id = f.message_id
    LEFT JOIN mti_brain_message q ON q.conversation_id = m.conversation_id AND q.role = 'user'
    WHERE f.embedding IS NOT NULL
      AND f.comment IS NOT NULL
      AND f.comment != ''
      AND f.thread_id != :current_thread_id
      AND 1 - (f.embedding <=> CAST(:embedding AS vector)) >= :min_similarity
    ORDER BY similarity DESC
    LIMIT :limit
""")


async def find_similar_feedback(
    db: AsyncSession,
    question: str,
    current_thread_id: uuid.UUID,
    limit: int = 5,
    min_similarity: float = 0.75,
) -> list[dict]:
    """Find feedback from OTHER threads with questions semantically similar to the current one.

    Current thread is excluded — find_thread_feedback already covers it completely.
    """
    embedding = await embed_question(question)
    if embedding is None:
        return []
    result = await db.execute(
        _FIND_SIMILAR_SQL,
        {
            "embedding": str(embedding),
            "current_thread_id": str(current_thread_id),
            "limit": limit,
            "min_similarity": min_similarity,
        },
    )
    return [
        {
            "id": str(row.id),
            "liked": row.liked,
            "comment": row.comment,
            "thread_id": str(row.thread_id),
            "question_text": (row.question_text or "")[:200],
            "similarity": round(float(row.similarity), 3),
            "source": "similar",
        }
        for row in result.fetchall()
    ]


def build_feedback_context(
    thread_feedback: list[dict],
    similar_feedback: list[dict],
) -> str:
    """Build a feedback context string to inject into agent prompts.

    Deduplicates by id (thread feedback takes priority over similar).
    Returns empty string if no actionable feedback exists.
    """
    seen_ids = {f["id"] for f in thread_feedback}
    similar_deduped = [f for f in similar_feedback if f["id"] not in seen_ids]
    all_feedback = thread_feedback + similar_deduped

    dislikes = [f for f in all_feedback if not f["liked"] and f.get("comment")]
    likes = [f for f in all_feedback if f["liked"] and f.get("comment")]

    if not dislikes and not likes:
        return ""

    lines = ["USER FEEDBACK (apply to your response):"]
    if dislikes:
        lines.append("  AVOID (users disliked this):")
        for fb in dislikes[:5]:
            source = "this thread" if fb.get("source") == "thread" else "similar question"
            lines.append(f"    - [{source}] {fb['comment']}")
    if likes:
        lines.append("  KEEP DOING (users liked this):")
        for fb in likes[:3]:
            source = "this thread" if fb.get("source") == "thread" else "similar question"
            lines.append(f"    - [{source}] {fb['comment']}")

    return "\n".join(lines)

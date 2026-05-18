"""Feedback service — persistence and retrieval for user thumbs-up/down."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.logger import logger
from app.models.conversation import MTIBrainFeedback
from app.services.embeddings import embed_question
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_FEEDBACK_STALE_DAYS = 90
_FEEDBACK_OLD_DAYS = 30


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
    ORDER BY f.created_at DESC
    LIMIT :limit
""")


async def find_thread_feedback(
    db: AsyncSession,
    thread_id: uuid.UUID,
    limit: int = 5,
) -> list[dict]:
    """Get all feedback from the current thread."""
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
            "created_at": row.created_at,
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
        f.created_at,
        q.content AS question_text,
        1 - (f.embedding <=> CAST(:embedding AS vector)) AS similarity
    FROM mti_brain_feedback f
    LEFT JOIN mti_brain_message m ON m.id = f.message_id
    LEFT JOIN mti_brain_message q ON q.conversation_id = m.conversation_id AND q.role = 'user'
    WHERE f.embedding IS NOT NULL
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
            "created_at": row.created_at,
            "question_text": (row.question_text or "")[:200],
            "similarity": round(float(row.similarity), 3),
            "source": "similar",
        }
        for row in result.fetchall()
    ]


def _days_old(created_at: datetime | None) -> int:
    if created_at is None:
        return 0
    now = datetime.now(timezone.utc)
    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    return max(0, (now - ts).days)


def build_feedback_context(
    thread_feedback: list[dict],
    similar_feedback: list[dict],
) -> str:
    """Build a feedback context string to inject into agent prompts.

    Deduplicates by id (thread feedback takes priority over similar).
    Excludes feedback older than 90 days. Annotates feedback 30-90 days old.
    Thumbs-only (no comment) signals are included with synthetic labels at lower priority.
    Returns empty string if no actionable feedback exists.
    """
    seen_ids = {f["id"] for f in thread_feedback}
    similar_deduped = [f for f in similar_feedback if f["id"] not in seen_ids]
    all_feedback = thread_feedback + similar_deduped

    # Exclude stale feedback
    all_feedback = [f for f in all_feedback if _days_old(f.get("created_at")) <= _FEEDBACK_STALE_DAYS]

    if not all_feedback:
        return ""

    def _label(fb: dict) -> str:
        age = _days_old(fb.get("created_at"))
        source = "this thread" if fb.get("source") == "thread" else "similar question"
        comment = fb.get("comment") or ""
        age_note = f" (from ~{age}d ago)" if age > _FEEDBACK_OLD_DAYS else ""
        if comment:
            return f"    - [{source}]{age_note} {comment}"
        sentiment = "User rated a similar response positively" if fb["liked"] else "User rated a similar response negatively"
        return f"    - [{source}]{age_note} [positive signal] {sentiment}" if fb["liked"] else f"    - [{source}]{age_note} [negative signal] {sentiment}"

    def _sort_key(fb: dict):
        age = _days_old(fb.get("created_at"))
        has_comment = 1 if fb.get("comment") else 0
        return (-has_comment, age)

    dislikes = sorted([f for f in all_feedback if not f["liked"]], key=_sort_key)
    likes = sorted([f for f in all_feedback if f["liked"]], key=_sort_key)

    if not dislikes and not likes:
        return ""

    lines = ["USER FEEDBACK (apply to your response):"]
    if dislikes:
        lines.append("  AVOID (users disliked this):")
        for fb in dislikes[:5]:
            lines.append(_label(fb))
    if likes:
        lines.append("  KEEP DOING (users liked this):")
        for fb in likes[:3]:
            lines.append(_label(fb))

    return "\n".join(lines)

"""Feedback service — persistence and retrieval for user thumbs-up/down."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.logger import logger
from app.models.conversation import MTIBrainFeedback
from app.services.embeddings import embed_question
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

_FEEDBACK_STALE_DAYS = 90
_FEEDBACK_OLD_DAYS = 30

_RRF_K = 60


async def save_feedback(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    thread_id: uuid.UUID,
    liked: bool,
    comment: str | None = None,
) -> tuple[MTIBrainFeedback, str | None, str | None, dict | None]:
    """Save feedback and embed the question for future similarity search.

    Embedding is async and non-blocking. If it fails, feedback is still
    saved — just without an embedding (similarity search won't find it).

    Returns ``(feedback, langfuse_trace_id, pattern_id, neo4j_context)``
    where neo4j_context carries the action + data needed for graph writes:
      action='dislike'              → write :AntiPattern (any confidence)
      action='like_without_pattern' → retroactively write :QueryPattern (low-confidence like)
      None                          → no extra graph write needed
    """
    result = await db.execute(
        text(
            "SELECT id, "
            "       metadata->>'langfuse_trace_id' AS langfuse_trace_id, "
            "       metadata->>'pattern_id' AS pattern_id, "
            "       metadata->>'sql' AS sql, "
            "       metadata->>'tables_used' AS tables_used, "
            "       metadata->>'intent' AS intent, "
            "       metadata->>'complexity' AS complexity, "
            "       metadata->'confidence'->>'score' AS confidence_score "
            "FROM mti_brain_message "
            "WHERE conversation_id = :cid AND role = 'assistant' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    row = result.one_or_none()
    message_id = row.id if row else None
    langfuse_trace_id: str | None = row.langfuse_trace_id if row else None
    pattern_id: str | None = row.pattern_id if row else None

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

    # Upsert: update existing feedback for this message rather than creating duplicates
    existing: MTIBrainFeedback | None = None
    if message_id:
        existing = (await db.execute(
            select(MTIBrainFeedback).where(MTIBrainFeedback.message_id == message_id)
        )).scalar_one_or_none()

    if existing:
        existing.liked = liked
        existing.comment = comment
        existing.question_text = question_text or None
        if embedding is not None:
            existing.embedding = embedding
        feedback = existing
        logger.info(
            f"Feedback updated: conversation={conversation_id}, liked={liked}"
        )
    else:
        feedback = MTIBrainFeedback(
            message_id=message_id,
            thread_id=thread_id,
            liked=liked,
            comment=comment,
            question_text=question_text or None,
            embedding=embedding,
        )
        db.add(feedback)
        logger.info(
            f"Feedback saved: conversation={conversation_id}, liked={liked}, "
            f"has_embedding={embedding is not None}"
        )

    await db.flush()

    _sql = (row.sql or "") if row else ""
    _tables = (row.tables_used or "") if row else ""
    _intent = (row.intent or "") if row else ""
    _complexity = (row.complexity or "") if row else ""
    _conf_score = int(row.confidence_score) if (row and row.confidence_score) else 0

    neo4j_context: dict | None = None
    if not liked:
        neo4j_context = {
            "action": "dislike",
            "question": question_text,
            "sql": _sql,
            "tables_used": _tables,
            "intent": _intent,
            "complexity": _complexity,
            "embedding": embedding,
            "comment": comment,
        }
    elif liked and not pattern_id:
        neo4j_context = {
            "action": "like_without_pattern",
            "question": question_text,
            "sql": _sql,
            "tables_used": _tables,
            "intent": _intent,
            "complexity": _complexity,
            "confidence_score": _conf_score,
            "embedding": embedding,
        }

    return feedback, langfuse_trace_id, pattern_id, neo4j_context


# question_text is now stored on the row — no JOIN to mti_brain_message needed
_FIND_THREAD_FEEDBACK_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text
    FROM mti_brain_feedback f
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


# ── Hybrid cross-thread search ────────────────────────────────────────────────

_FIND_SIMILAR_VECTOR_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text,
        1 - (f.embedding <=> CAST(:embedding AS vector)) AS score
    FROM mti_brain_feedback f
    WHERE f.embedding IS NOT NULL
      AND f.thread_id != :current_thread_id
      AND 1 - (f.embedding <=> CAST(:embedding AS vector)) >= :min_similarity
    ORDER BY score DESC
    LIMIT :limit
""")

_FIND_SIMILAR_FTS_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text,
        ts_rank_cd(f.search_vector, websearch_to_tsquery('english', :query)) AS score
    FROM mti_brain_feedback f
    WHERE f.search_vector IS NOT NULL
      AND f.thread_id != :current_thread_id
      AND f.search_vector @@ websearch_to_tsquery('english', :query)
    ORDER BY score DESC
    LIMIT :limit
""")


def _rrf_merge(
    ranked_lists: list[list[dict]],
    k: int = _RRF_K,
    top_n: int = 5,
) -> list[dict]:
    """Reciprocal Rank Fusion over N ranked lists keyed by feedback row id."""
    scores: dict[str, float] = {}
    by_key: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, row in enumerate(ranked):
            key = row["id"]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_key.setdefault(key, row)
    ranked_keys = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result = []
    for key, score in ranked_keys[:top_n]:
        row = dict(by_key[key])
        row["_rrf_score"] = score
        result.append(row)
    return result


async def find_similar_feedback(
    db: AsyncSession,
    question: str,
    current_thread_id: uuid.UUID,
    limit: int = 5,
    min_similarity: float = 0.60,
) -> list[dict]:
    """Find feedback from OTHER threads matching the current question.

    Hybrid: vector cosine similarity + FTS over question_text and comment,
    merged via Reciprocal Rank Fusion. Both legs are optional — if one fails
    or returns nothing the other still contributes results.
    """
    ranked_lists: list[list[dict]] = []

    # Vector leg
    embedding = await embed_question(question)
    if embedding is not None:
        try:
            vec_result = await db.execute(
                _FIND_SIMILAR_VECTOR_SQL,
                {
                    "embedding": str(embedding),
                    "current_thread_id": str(current_thread_id),
                    "limit": limit * 2,
                    "min_similarity": min_similarity,
                },
            )
            vec_rows = [
                {
                    "id": str(r.id),
                    "liked": r.liked,
                    "comment": r.comment,
                    "thread_id": str(r.thread_id),
                    "created_at": r.created_at,
                    "question_text": (r.question_text or "")[:200],
                    "similarity": round(float(r.score), 3),
                }
                for r in vec_result.fetchall()
            ]
            if vec_rows:
                ranked_lists.append(vec_rows)
        except Exception as exc:
            logger.warning("find_similar_feedback | vector_leg_failed | err={}: {}", type(exc).__name__, exc)

    # FTS leg — searches both question_text and comment via search_vector
    try:
        fts_result = await db.execute(
            _FIND_SIMILAR_FTS_SQL,
            {
                "query": question,
                "current_thread_id": str(current_thread_id),
                "limit": limit * 2,
            },
        )
        fts_rows = [
            {
                "id": str(r.id),
                "liked": r.liked,
                "comment": r.comment,
                "thread_id": str(r.thread_id),
                "created_at": r.created_at,
                "question_text": (r.question_text or "")[:200],
                "similarity": None,
            }
            for r in fts_result.fetchall()
        ]
        if fts_rows:
            ranked_lists.append(fts_rows)
    except Exception as exc:
        logger.warning("find_similar_feedback | fts_leg_failed | err={}: {}", type(exc).__name__, exc)

    if not ranked_lists:
        return []

    merged = _rrf_merge(ranked_lists, top_n=limit)
    return [
        {**row, "source": "similar"}
        for row in merged
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

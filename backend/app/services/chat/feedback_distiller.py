"""Background distillation of user feedback into a concise behavioural profile.

Triggered after each feedback save when the comment-feedback count has grown
by ≥ 5 since the last distillation. Produces a 5–8 bullet profile stored on
mti_brain_user.distilled_preferences for injection into future pipelines.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from app.core.logger import logger
from app.models.user import MTIBrainUser
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

_DISTILL_DELTA = 5
_MAX_FEEDBACK_ROWS = 50

_DISTILL_PROMPT = """You are summarising feedback that a user has given about AI-generated analytics responses.
Below are the user's feedback comments (likes and dislikes). Produce exactly 5–8 concise bullet points
that describe this user's consistent preferences and things they want to avoid.
Focus on specific, actionable behaviours (e.g. "Prefers CTEs over subqueries", "Always show a chart", "Avoid raw percentages without context").
Reply with ONLY the bullet points, no intro or outro.

FEEDBACK COMMENTS:
{feedback_text}"""


async def maybe_distill(user_id: uuid.UUID, session_factory: Callable) -> None:
    """Fire-and-forget: distill if comment-feedback count has grown by ≥5 since last distillation."""
    try:
        async with session_factory() as db:
            row = await db.execute(
                select(MTIBrainUser.feedback_count_at_distill)
                .where(MTIBrainUser.id == user_id)
            )
            count_at_distill = row.scalar_one_or_none() or 0

            from app.models.conversation import MTIBrainFeedback
            from app.models.conversation import MTIBrainThread
            current_count_row = await db.execute(
                select(func.count(MTIBrainFeedback.id))
                .join(MTIBrainThread, MTIBrainFeedback.thread_id == MTIBrainThread.id)
                .where(MTIBrainThread.user_id == user_id)
                .where(MTIBrainFeedback.comment.is_not(None))
            )
            current_count = current_count_row.scalar_one() or 0

            if current_count - count_at_distill >= _DISTILL_DELTA:
                await _distill_user_feedback(user_id, db, current_count)
                await db.commit()
    except Exception as exc:
        logger.warning("feedback_distiller | maybe_distill failed | user={} | err={}", user_id, exc)


async def _distill_user_feedback(
    user_id: uuid.UUID,
    db: AsyncSession,
    current_count: int,
) -> None:
    from app.models.conversation import MTIBrainFeedback, MTIBrainThread

    rows = await db.execute(
        select(MTIBrainFeedback.liked, MTIBrainFeedback.comment)
        .join(MTIBrainThread, MTIBrainFeedback.thread_id == MTIBrainThread.id)
        .where(MTIBrainThread.user_id == user_id)
        .where(MTIBrainFeedback.comment.is_not(None))
        .order_by(MTIBrainFeedback.created_at.desc())
        .limit(_MAX_FEEDBACK_ROWS)
    )
    feedback_rows = rows.all()
    if not feedback_rows:
        return

    lines = []
    for liked, comment in feedback_rows:
        sentiment = "LIKED" if liked else "DISLIKED"
        lines.append(f"[{sentiment}] {comment}")
    feedback_text = "\n".join(lines)

    try:
        from app.services.agents.bedrock import get_llm
        llm = get_llm("fast")
        response = await llm.ainvoke(_DISTILL_PROMPT.format(feedback_text=feedback_text))
        profile = (response.content or "").strip()
        if not profile:
            return
    except Exception as exc:
        logger.warning("feedback_distiller | LLM call failed | user={} | err={}", user_id, exc)
        return

    await db.execute(
        text(
            "UPDATE mti_brain_user "
            "SET distilled_preferences = :profile, "
            "    distilled_at = :now, "
            "    feedback_count_at_distill = :count "
            "WHERE id = :uid"
        ),
        {
            "profile": profile,
            "now": datetime.now(timezone.utc),
            "count": current_count,
            "uid": str(user_id),
        },
    )
    logger.info(
        "feedback_distiller | distilled | user={} | feedback_count={} | bullets={}",
        user_id, current_count, len([l for l in profile.splitlines() if l.strip()]),
    )

"""Settings API — CRUD for user standing instructions + feedback history."""

import math
import uuid
from datetime import datetime, timezone

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import get_async_session, get_read_session
from app.models.conversation import MTIBrainFeedback, MTIBrainThread
from app.models.user_instruction import UserInstruction
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_VALID_SCOPES = {"all", "written_answers", "sql_only"}


class InstructionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    content: str
    enabled: bool
    scope: str
    created_at: datetime
    updated_at: datetime


class InstructionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    enabled: bool = True
    scope: str = Field("all")

    def model_post_init(self, __context):
        if self.scope not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {_VALID_SCOPES}")


class InstructionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    enabled: bool | None = None
    scope: str | None = None

    def model_post_init(self, __context):
        if self.scope is not None and self.scope not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {_VALID_SCOPES}")


@router.get("/instructions", response_model=list[InstructionOut])
async def list_instructions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    result = await db.execute(
        select(UserInstruction)
        .where(UserInstruction.user_id == current_user.id)
        .order_by(UserInstruction.created_at)
    )
    return [InstructionOut.model_validate(row) for row in result.scalars().all()]


@router.post("/instructions", response_model=InstructionOut, status_code=201)
async def create_instruction(
    body: InstructionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    now = datetime.now(timezone.utc)
    instruction = UserInstruction(
        user_id=current_user.id,
        title=body.title,
        content=body.content,
        enabled=body.enabled,
        scope=body.scope,
        created_at=now,
        updated_at=now,
    )
    db.add(instruction)
    await db.flush()
    await db.commit()
    await db.refresh(instruction)
    return InstructionOut.model_validate(instruction)


@router.patch("/instructions/{instruction_id}", response_model=InstructionOut)
async def update_instruction(
    instruction_id: uuid.UUID,
    body: InstructionUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    result = await db.execute(
        select(UserInstruction).where(
            UserInstruction.id == instruction_id,
            UserInstruction.user_id == current_user.id,
        )
    )
    instruction = result.scalar_one_or_none()
    if not instruction:
        raise HTTPException(status_code=404, detail="Instruction not found")

    if body.title is not None:
        instruction.title = body.title
    if body.content is not None:
        instruction.content = body.content
    if body.enabled is not None:
        instruction.enabled = body.enabled
    if body.scope is not None:
        instruction.scope = body.scope
    instruction.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.commit()
    await db.refresh(instruction)
    return InstructionOut.model_validate(instruction)


class FeedbackHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    liked: bool | None
    comment: str | None
    question_text: str | None
    thread_id: uuid.UUID
    thread_title: str | None
    feedback_type: str | None
    last_triggered_at: datetime | None
    trigger_count: int
    created_at: datetime


class FeedbackHistoryPage(BaseModel):
    items: list[FeedbackHistoryItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@router.get("/feedback", response_model=FeedbackHistoryPage)
async def list_feedback_history(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    """Return paginated feedback history for the current user, newest first."""
    _filters = (
        MTIBrainThread.user_id == current_user.id,
        MTIBrainFeedback.liked.is_not(None),
    )

    total_result = await db.execute(
        select(func.count(MTIBrainFeedback.id))
        .join(MTIBrainThread, MTIBrainFeedback.thread_id == MTIBrainThread.id)
        .where(*_filters)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(
            MTIBrainFeedback.id,
            MTIBrainFeedback.liked,
            MTIBrainFeedback.comment,
            MTIBrainFeedback.question_text,
            MTIBrainFeedback.thread_id,
            MTIBrainFeedback.feedback_type,
            MTIBrainFeedback.last_triggered_at,
            MTIBrainFeedback.trigger_count,
            MTIBrainFeedback.created_at,
            MTIBrainThread.title.label("thread_title"),
        )
        .join(MTIBrainThread, MTIBrainFeedback.thread_id == MTIBrainThread.id)
        .where(*_filters)
        .order_by(MTIBrainFeedback.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )

    items = [
        FeedbackHistoryItem(
            id=r.id,
            liked=r.liked,
            comment=r.comment,
            question_text=r.question_text,
            thread_id=r.thread_id,
            thread_title=r.thread_title,
            feedback_type=r.feedback_type,
            last_triggered_at=r.last_triggered_at,
            trigger_count=r.trigger_count or 0,
            created_at=r.created_at,
        )
        for r in rows_result.all()
    ]

    return FeedbackHistoryPage(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=max(1, math.ceil(total / per_page)),
    )


_PATTERN_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
    "to", "of", "in", "on", "at", "for", "with", "by", "this", "that",
    "it", "its", "not", "no", "do", "don't", "please", "use", "using",
    "always", "never", "should", "would", "could", "will", "when", "show",
    "instead", "more", "less", "also", "just", "only", "very", "much",
}


def _topic_key(comment: str) -> str:
    words = [
        w.strip(".,!?;:()") for w in comment.lower().split()
        if len(w.strip(".,!?;:()")) > 3 and w.strip(".,!?;:()") not in _PATTERN_STOPWORDS
    ]
    return " ".join(words[:5])


def _suggest_title(topic_key: str, is_positive: bool) -> str:
    words = topic_key.replace("_", " ").title()
    prefix = "Always" if is_positive else "Avoid"
    return f"{prefix}: {words}"


class FeedbackPattern(BaseModel):
    topic_key: str
    count: int
    liked_count: int
    disliked_count: int
    sample_comments: list[str]
    suggested_title: str
    feedback_ids: list[uuid.UUID]


@router.get("/feedback/patterns", response_model=list[FeedbackPattern])
async def get_feedback_patterns(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    """Return recurring feedback topics — patterns appearing in 2+ feedback comments."""
    rows_result = await db.execute(
        select(
            MTIBrainFeedback.id,
            MTIBrainFeedback.liked,
            MTIBrainFeedback.comment,
        )
        .join(MTIBrainThread, MTIBrainFeedback.thread_id == MTIBrainThread.id)
        .where(
            MTIBrainThread.user_id == current_user.id,
            MTIBrainFeedback.comment.is_not(None),
        )
        .order_by(MTIBrainFeedback.created_at.desc())
        .limit(100)
    )
    rows = rows_result.all()

    groups: dict[str, dict] = {}
    for row in rows:
        key = _topic_key(row.comment or "")
        if not key:
            continue
        if key not in groups:
            groups[key] = {
                "topic_key": key,
                "count": 0,
                "liked_count": 0,
                "disliked_count": 0,
                "sample_comments": [],
                "feedback_ids": [],
            }
        g = groups[key]
        g["count"] += 1
        if row.liked:
            g["liked_count"] += 1
        else:
            g["disliked_count"] += 1
        if len(g["sample_comments"]) < 3:
            g["sample_comments"].append(row.comment)
        g["feedback_ids"].append(row.id)

    patterns = sorted(
        [g for g in groups.values() if g["count"] >= 2],
        key=lambda p: p["count"],
        reverse=True,
    )
    return [
        FeedbackPattern(
            **g,
            suggested_title=_suggest_title(g["topic_key"], g["liked_count"] >= g["disliked_count"]),
        )
        for g in patterns[:20]
    ]


@router.delete("/instructions/{instruction_id}", status_code=204)
async def delete_instruction(
    instruction_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    result = await db.execute(
        select(UserInstruction).where(
            UserInstruction.id == instruction_id,
            UserInstruction.user_id == current_user.id,
        )
    )
    instruction = result.scalar_one_or_none()
    if not instruction:
        raise HTTPException(status_code=404, detail="Instruction not found")
    await db.delete(instruction)
    await db.commit()

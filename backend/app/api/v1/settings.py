"""Settings API — CRUD for user standing instructions + feedback history."""

import asyncio
import math
import re as _re
import uuid
from datetime import datetime, timezone

_LUCENE_SAFE = _re.compile(r'[^\w\s]', _re.UNICODE)
_MAX_FTS_TOKENS = 8  # cap matches table_search._fuzzy_fts; prevents TooManyClauses


def _and_fts(text: str) -> str:
    """Build a Lucene query where ALL words must match (AND semantics).

    Uses + prefix so every token is required, plus ~ fuzzy for tokens >= 3 chars.
    'invoice number' → '+invoice~ +number~'
    Token count is capped at _MAX_FTS_TOKENS to prevent TooManyClauses errors.
    """
    tokens = []
    for raw in text.split():
        t = _LUCENE_SAFE.sub("", raw)
        if not t:
            continue
        tokens.append(f"+{t}~" if len(t) >= 3 else f"+{t}")
        if len(tokens) >= _MAX_FTS_TOKENS:
            break
    return " ".join(tokens) if tokens else text

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


_SKIP_KEYS = {"cohere_embedding"}


def _serialize_props(props: dict) -> dict:
    """Convert Neo4j property types to JSON-safe values, drop embedding vectors."""
    out = {}
    for k, v in props.items():
        if k in _SKIP_KEYS:
            continue
        if hasattr(v, "iso_format"):
            out[k] = v.iso_format()
        elif isinstance(v, list):
            out[k] = [item.iso_format() if hasattr(item, "iso_format") else item for item in v]
        else:
            out[k] = v
    return out


class PatternListPage(BaseModel):
    items: list
    total: int
    enabled_total: int
    disabled_total: int
    skip: int
    limit: int


@router.get("/admin/query-patterns", response_model=PatternListPage)
async def list_query_patterns(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    search: str = Query(default=""),
    filter: str = Query(default="all", pattern="^(all|enabled|disabled)$"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List QueryPattern nodes with pagination, search, and enabled filter — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run

    def _fetch():
        from app.core.logger import logger as _log
        q = search.strip()
        use_fts = bool(q)
        fts_q = _and_fts(q) if use_fts else ""

        filter_extra = (
            "AND qp.is_enabled = true" if filter == "enabled"
            else "AND (qp.is_enabled = false OR qp.is_enabled IS NULL)" if filter == "disabled"
            else ""
        )

        params = {"q": fts_q, "skip": skip, "limit": limit}

        if use_fts:
            try:
                count_rows = _neo4j_run(
                    f"CALL db.index.fulltext.queryNodes('querypattern_fts', $q) YIELD node AS qp "
                    f"WHERE true {filter_extra} RETURN count(qp) AS total",
                    params,
                )
                enabled_rows = _neo4j_run(
                    "CALL db.index.fulltext.queryNodes('querypattern_fts', $q) YIELD node AS qp "
                    "WHERE qp.is_enabled = true RETURN count(qp) AS n",
                    params,
                )
                disabled_rows = _neo4j_run(
                    "CALL db.index.fulltext.queryNodes('querypattern_fts', $q) YIELD node AS qp "
                    "WHERE (qp.is_enabled = false OR qp.is_enabled IS NULL) RETURN count(qp) AS n",
                    params,
                )
                rows = _neo4j_run(
                    f"CALL db.index.fulltext.queryNodes('querypattern_fts', $q) YIELD node AS qp, score "
                    f"WHERE true {filter_extra} "
                    f"RETURN properties(qp) AS props, score AS _sort "
                    f"ORDER BY _sort DESC SKIP $skip LIMIT $limit",
                    params,
                )
                total = count_rows[0]["total"] if count_rows else 0
                enabled_total = enabled_rows[0]["n"] if enabled_rows else 0
                disabled_total = disabled_rows[0]["n"] if disabled_rows else 0
                items = [_serialize_props(dict(r["props"] or {})) for r in rows]
                return total, enabled_total, disabled_total, items
            except Exception as e:
                _log.warning("settings | list_query_patterns fts failed, falling back to scan | error={}", e)
                use_fts = False

        filter_where = f"WHERE {filter_extra[4:]}" if filter_extra else ""
        count_rows = _neo4j_run(
            f"MATCH (qp:QueryPattern) {filter_where} RETURN count(qp) AS total", params
        )
        enabled_rows = _neo4j_run(
            "MATCH (qp:QueryPattern) WHERE qp.is_enabled = true RETURN count(qp) AS n", params
        )
        disabled_rows = _neo4j_run(
            "MATCH (qp:QueryPattern) WHERE (qp.is_enabled = false OR qp.is_enabled IS NULL) RETURN count(qp) AS n",
            params,
        )
        rows = _neo4j_run(
            f"MATCH (qp:QueryPattern) {filter_where} "
            f"RETURN properties(qp) AS props, coalesce(qp.occurrence_count, 0) AS _sort "
            f"ORDER BY _sort DESC, qp.id ASC SKIP $skip LIMIT $limit",
            params,
        )
        total = count_rows[0]["total"] if count_rows else 0
        enabled_total = enabled_rows[0]["n"] if enabled_rows else 0
        disabled_total = disabled_rows[0]["n"] if disabled_rows else 0
        items = [_serialize_props(dict(r["props"] or {})) for r in rows]
        return total, enabled_total, disabled_total, items

    total, enabled_total, disabled_total, items = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return PatternListPage(items=items, total=total, enabled_total=enabled_total, disabled_total=disabled_total, skip=skip, limit=limit)


@router.get("/admin/anti-patterns", response_model=PatternListPage)
async def list_anti_patterns(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    search: str = Query(default=""),
    filter: str = Query(default="all", pattern="^(all|enabled|disabled)$"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List AntiPattern nodes with pagination, search, and enabled filter — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run

    def _fetch():
        from app.core.logger import logger as _log
        q = search.strip()
        use_fts = bool(q)
        fts_q = _and_fts(q) if use_fts else ""

        filter_extra = (
            "AND ap.is_enabled = true" if filter == "enabled"
            else "AND (ap.is_enabled = false OR ap.is_enabled IS NULL)" if filter == "disabled"
            else ""
        )

        params = {"q": fts_q, "skip": skip, "limit": limit}

        if use_fts:
            try:
                count_rows = _neo4j_run(
                    f"CALL db.index.fulltext.queryNodes('antipattern_fts', $q) YIELD node AS ap "
                    f"WHERE true {filter_extra} RETURN count(ap) AS total",
                    params,
                )
                enabled_rows = _neo4j_run(
                    "CALL db.index.fulltext.queryNodes('antipattern_fts', $q) YIELD node AS ap "
                    "WHERE ap.is_enabled = true RETURN count(ap) AS n",
                    params,
                )
                disabled_rows = _neo4j_run(
                    "CALL db.index.fulltext.queryNodes('antipattern_fts', $q) YIELD node AS ap "
                    "WHERE (ap.is_enabled = false OR ap.is_enabled IS NULL) RETURN count(ap) AS n",
                    params,
                )
                rows = _neo4j_run(
                    f"CALL db.index.fulltext.queryNodes('antipattern_fts', $q) YIELD node AS ap, score "
                    f"WHERE true {filter_extra} "
                    f"RETURN properties(ap) AS props, score AS _sort "
                    f"ORDER BY _sort DESC SKIP $skip LIMIT $limit",
                    params,
                )
                total = count_rows[0]["total"] if count_rows else 0
                enabled_total = enabled_rows[0]["n"] if enabled_rows else 0
                disabled_total = disabled_rows[0]["n"] if disabled_rows else 0
                items = [_serialize_props(dict(r["props"] or {})) for r in rows]
                return total, enabled_total, disabled_total, items
            except Exception as e:
                _log.warning("settings | list_anti_patterns fts failed, falling back to scan | error={}", e)
                use_fts = False

        filter_where = f"WHERE {filter_extra[4:]}" if filter_extra else ""
        count_rows = _neo4j_run(
            f"MATCH (ap:AntiPattern) {filter_where} RETURN count(ap) AS total", params
        )
        enabled_rows = _neo4j_run(
            "MATCH (ap:AntiPattern) WHERE ap.is_enabled = true RETURN count(ap) AS n", params
        )
        disabled_rows = _neo4j_run(
            "MATCH (ap:AntiPattern) WHERE (ap.is_enabled = false OR ap.is_enabled IS NULL) RETURN count(ap) AS n",
            params,
        )
        rows = _neo4j_run(
            f"MATCH (ap:AntiPattern) {filter_where} "
            f"RETURN properties(ap) AS props, coalesce(ap.occurrence_count, 0) AS _sort "
            f"ORDER BY _sort DESC, ap.id ASC SKIP $skip LIMIT $limit",
            params,
        )
        total = count_rows[0]["total"] if count_rows else 0
        enabled_total = enabled_rows[0]["n"] if enabled_rows else 0
        disabled_total = disabled_rows[0]["n"] if disabled_rows else 0
        items = [_serialize_props(dict(r["props"] or {})) for r in rows]
        return total, enabled_total, disabled_total, items

    total, enabled_total, disabled_total, items = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return PatternListPage(items=items, total=total, enabled_total=enabled_total, disabled_total=disabled_total, skip=skip, limit=limit)


@router.get("/query-patterns/enabled")
async def list_enabled_query_patterns(
    current_user: CurrentUser = Depends(get_current_user),
):
    """List enabled QueryPattern nodes — any authenticated user."""
    from app.services.agents.neo4j_client import _neo4j_run

    def _fetch():
        rows = _neo4j_run(
            """
            MATCH (qp:QueryPattern {is_enabled: true})
            RETURN properties(qp) AS props
            ORDER BY coalesce(qp.occurrence_count, 0) DESC
            """,
            {},
        )
        items = [_serialize_props(dict(r["props"] or {})) for r in rows]
        return items

    items = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return {"items": items, "total": len(items)}


@router.get("/anti-patterns/enabled")
async def list_enabled_anti_patterns(
    current_user: CurrentUser = Depends(get_current_user),
):
    """List enabled AntiPattern nodes — any authenticated user."""
    from app.services.agents.neo4j_client import _neo4j_run

    def _fetch():
        rows = _neo4j_run(
            """
            MATCH (ap:AntiPattern {is_enabled: true})
            RETURN properties(ap) AS props
            ORDER BY coalesce(ap.occurrence_count, 0) DESC
            """,
            {},
        )
        items = [_serialize_props(dict(r["props"] or {})) for r in rows]
        return items

    items = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return {"items": items, "total": len(items)}


class PatternEnabledUpdate(BaseModel):
    is_enabled: bool


@router.patch("/admin/query-patterns/{pattern_id}/enabled")
async def set_query_pattern_enabled(
    pattern_id: str,
    body: PatternEnabledUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Set is_enabled on a QueryPattern node — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run, _neo4j_write

    def _update():
        rows = _neo4j_run(
            "MATCH (qp:QueryPattern {id: $id}) RETURN count(qp) AS found",
            {"id": pattern_id},
        )
        if not rows or rows[0]["found"] == 0:
            return False
        _neo4j_write(
            "MATCH (qp:QueryPattern {id: $id}) SET qp.is_enabled = $is_enabled",
            id=pattern_id,
            is_enabled=body.is_enabled,
        )
        return True

    found = await asyncio.get_event_loop().run_in_executor(None, _update)
    if not found:
        raise HTTPException(status_code=404, detail="QueryPattern not found")
    return {"id": pattern_id, "is_enabled": body.is_enabled}


@router.patch("/admin/anti-patterns/{pattern_id}/enabled")
async def set_anti_pattern_enabled(
    pattern_id: str,
    body: PatternEnabledUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Set is_enabled on an AntiPattern node — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run, _neo4j_write

    def _update():
        rows = _neo4j_run(
            "MATCH (ap:AntiPattern {id: $id}) RETURN count(ap) AS found",
            {"id": pattern_id},
        )
        if not rows or rows[0]["found"] == 0:
            return False
        _neo4j_write(
            "MATCH (ap:AntiPattern {id: $id}) SET ap.is_enabled = $is_enabled",
            id=pattern_id,
            is_enabled=body.is_enabled,
        )
        return True

    found = await asyncio.get_event_loop().run_in_executor(None, _update)
    if not found:
        raise HTTPException(status_code=404, detail="AntiPattern not found")
    return {"id": pattern_id, "is_enabled": body.is_enabled}


@router.delete("/admin/query-patterns/{pattern_id}", status_code=204)
async def delete_query_pattern(
    pattern_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a QueryPattern node — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run, _neo4j_write

    def _delete():
        rows = _neo4j_run(
            "MATCH (qp:QueryPattern {id: $id}) RETURN count(qp) AS found",
            {"id": pattern_id},
        )
        if not rows or rows[0]["found"] == 0:
            return False
        _neo4j_write("MATCH (qp:QueryPattern {id: $id}) DETACH DELETE qp", id=pattern_id)
        return True

    found = await asyncio.get_event_loop().run_in_executor(None, _delete)
    if not found:
        raise HTTPException(status_code=404, detail="QueryPattern not found")


@router.delete("/admin/anti-patterns/{pattern_id}", status_code=204)
async def delete_anti_pattern(
    pattern_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete an AntiPattern node — admin only."""
    if "admin" not in current_user.groups:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.agents.neo4j_client import _neo4j_run, _neo4j_write

    def _delete():
        rows = _neo4j_run(
            "MATCH (ap:AntiPattern {id: $id}) RETURN count(ap) AS found",
            {"id": pattern_id},
        )
        if not rows or rows[0]["found"] == 0:
            return False
        _neo4j_write("MATCH (ap:AntiPattern {id: $id}) DETACH DELETE ap", id=pattern_id)
        return True

    found = await asyncio.get_event_loop().run_in_executor(None, _delete)
    if not found:
        raise HTTPException(status_code=404, detail="AntiPattern not found")


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

"""Chat API endpoints for thread management and Q&A streaming."""

import asyncio
import datetime
import json
import time
import uuid


def _json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    import decimal
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _make_json_safe(obj):
    """Recursively convert datetime/Decimal to JSON primitives.

    SQLAlchemy serializes JSONB columns with its own json.dumps (no custom
    encoder), so we must sanitize the structure before handing it over.
    Handles dicts, lists, tuples, datetime.date/datetime, decimal.Decimal.
    """
    import decimal
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.logger import logger
from app.db import get_async_session, get_read_session
from app.schemas.chat import (
    AskRequest,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkMoveRequest,
    BulkMoveResponse,
    DeleteResponse,
    EditRequest,
    FeedbackOut,
    FeedbackRequest,
    MessageOut,
    MoveRequest,
    NewChatRequest,
    NewChatResponse,
    RenameRequest,
    RetryRequest,
    SearchResult,
    ThreadDetail,
    ThreadSummary,
)
from app.core.config import settings
from app.core.rate_limit import limiter
from app.services.chat import conversation as conv_service
from app.services.chat import feedback as fb_service
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

_active_streams: dict[str, asyncio.Event] = {}


def _get_display_name(user: CurrentUser) -> str:
    if user.name:
        return user.name
    prefix = user.email.split("@")[0].split(".")[0]
    return prefix.capitalize() if prefix else ""


def cancel_stream(thread_id: str) -> bool:
    ev = _active_streams.get(thread_id)
    if ev and not ev.is_set():
        ev.set()
        return True
    return False


_TONE_TO_PERSONA = {
    "analyst": "Analyst",
    "manager": "Manager",
    "director": "Director",
    "executive": "Executive",
}


def _build_sse_generator(
    question: str,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    parent_conversation_id: uuid.UUID | None,
    generate_title: bool = False,
    user_id: str | None = None,
    request_time: float | None = None,
    persona: str | None = None,
    max_rows: int = 100,
    deep_analysis: bool = False,
    cancel_event: asyncio.Event | None = None,
    prior_sql: str = "",
    user_display_name: str = "",
    user_email: str | None = None,
):
    async def _save_assistant_message(save_data: dict) -> None:
        from app.db import async_session_factory

        reasoning = save_data.get("reasoning")
        if isinstance(reasoning, list):
            reasoning = json.dumps(reasoning)

        msg_kwargs = dict(
            thread_id=thread_id,
            conversation_id=conversation_id,
            parent_conversation_id=parent_conversation_id,
            role="assistant",
            content=save_data.get("answer", ""),
            reasoning=reasoning,
            metadata=_make_json_safe({
                "sql": save_data.get("sql", ""),
                "columns": save_data.get("columns", []),
                "rows": save_data.get("rows", []),
                "row_count": save_data.get("row_count", 0),
                "chart_spec": save_data.get("chart_spec"),
                "follow_ups": save_data.get("follow_ups", []),
                "run_id": save_data.get("run_id", ""),
                "stopped": save_data.get("stopped", False),
                "duration_ms": save_data.get("duration_ms"),
                "pipeline_steps": save_data.get("pipeline_steps") or [],
                "question_type": save_data.get("question_type", ""),
                "persona": save_data.get("persona", ""),
                "no_data": save_data.get("no_data", False),
                "reliability_flags": save_data.get("reliability_flags", []),
                "token_usage": save_data.get("token_usage"),
                "langfuse_trace_id": save_data.get("langfuse_trace_id"),
                "langfuse_trace_url": save_data.get("langfuse_trace_url"),
            }),
        )
        try:
            async with async_session_factory() as save_db:
                await conv_service.save_message(save_db, **msg_kwargs)
                await conv_service.touch_thread(save_db, thread_id)
                await save_db.commit()
        except Exception:
            logger.exception("Failed to save assistant message")

    async def event_generator():
        from app.services.neo4j_analytics.graph import stream_pipeline
        from app.services.chat.feedback import (
            build_feedback_context,
            find_similar_feedback,
            find_thread_feedback,
        )

        _stream_start = request_time or time.perf_counter()
        _cancel = cancel_event if cancel_event is not None else asyncio.Event()
        _active_streams[str(thread_id)] = _cancel

        # ── Fetch feedback context (non-blocking, 1.5s timeout) ──────────────
        feedback_context = ""
        try:
            from app.db import async_session_factory
            async with async_session_factory() as _fb_db:
                _tf, _sf = await asyncio.gather(
                    asyncio.wait_for(
                        find_thread_feedback(_fb_db, thread_id, limit=5), timeout=1.5
                    ),
                    asyncio.wait_for(
                        find_similar_feedback(_fb_db, question, current_thread_id=thread_id, limit=5), timeout=1.5
                    ),
                    return_exceptions=True,
                )
                thread_fb = _tf if not isinstance(_tf, Exception) else []
                similar_fb = _sf if not isinstance(_sf, Exception) else []
                feedback_context = build_feedback_context(thread_fb, similar_fb)
                if feedback_context:
                    logger.info(
                        f"[sse] feedback context injected for thread={thread_id}: "
                        f"thread={len(thread_fb)} entries, similar={len(similar_fb)} entries"
                    )
        except Exception:
            pass  # pipeline runs without feedback — never block on this

        try:
            _elapsed = int((time.perf_counter() - _stream_start) * 1000)
            logger.info(
                f"[sse] generator first yield for thread {thread_id}: "
                f"elapsed_since_request={_elapsed}ms"
            )
            yield {"event": "timing.sync", "data": json.dumps({"elapsed_ms": _elapsed})}

            if generate_title:
                title = conv_service.make_title(question)
                if title:
                    yield {
                        "event": "title.generated",
                        "data": json.dumps({"thread_id": str(thread_id), "title": title}),
                    }

            # Track partial answer tokens so we can save them if the user stops.
            _partial_answer: list[str] = []

            async for sse_ev in stream_pipeline(
                question=question,
                thread_id=str(thread_id),
                persona=persona,
                user_id=user_id,
                max_rows=max_rows,
                deep_analysis=deep_analysis,
                cancel_event=_cancel,
                feedback_context=feedback_context,
                prior_sql=prior_sql,
                user_email=user_email,
                user_display_name=user_display_name,
            ):
                event_name = sse_ev["event"]
                data = sse_ev["data"]

                if event_name == "stopped":
                    duration_ms = int((time.perf_counter() - _stream_start) * 1000)
                    data = {**data, "conversation_id": str(conversation_id), "duration_ms": duration_ms}
                    yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}
                    asyncio.create_task(_save_assistant_message({
                        "answer": "".join(_partial_answer),
                        "stopped": True,
                        "duration_ms": duration_ms,
                    }))
                    break

                if event_name == "done":
                    data = {**data, "conversation_id": str(conversation_id)}
                    yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}
                    asyncio.create_task(_save_assistant_message(data))
                    break

                if event_name == "answer.delta":
                    _partial_answer.append(data.get("text", ""))

                yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}

                if event_name == "error":
                    break

        except Exception as e:
            logger.exception(f"Stream error for thread {thread_id}: {e}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "Something went wrong while processing your question.",
                    "conversation_id": str(conversation_id),
                }),
            }

        except BaseException:
            # GeneratorExit (client disconnect / navigation). Save a stopped
            # record so the DB is not left with a dangling unsaved message.
            duration_ms = int((time.perf_counter() - _stream_start) * 1000)
            asyncio.create_task(_save_assistant_message({
                "answer": "", "stopped": True, "duration_ms": duration_ms,
            }))
            raise

        finally:
            # Identity check: only remove OUR event. A subsequent ask/retry/edit
            # may have already registered a new cancel_event for the same thread.
            if _active_streams.get(str(thread_id)) is _cancel:
                _active_streams.pop(str(thread_id), None)

    return event_generator


# ─── POST /chat/new ───


@router.post("/new", response_model=NewChatResponse, status_code=201)
async def create_chat(
    body: NewChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    thread = await conv_service.create_thread(
        db,
        thread_id=body.thread_id,
        project_id=body.project_id,
        title=body.title,
        user_id=current_user.id,
    )
    return NewChatResponse(thread_id=thread.id, title=thread.title)


# ─── GET /chat/recents ───


@router.get("/recents")
async def list_recent_chats(
    search: str | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    starred: bool | None = Query(default=None),
    label: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
) -> list[ThreadSummary] | list[SearchResult]:
    if search and search.strip():
        results = await conv_service.search_threads(
            db,
            search_text=search.strip(),
            project_id=project_id,
            user_id=current_user.id,
            starred=starred,
            limit=limit,
            offset=offset,
        )
        return [SearchResult(**r) for r in results]

    threads = await conv_service.list_threads(
        db, project_id=project_id, limit=limit, offset=offset,
        user_id=current_user.id, starred=starred, label=label,
    )
    return [ThreadSummary(**t) for t in threads]


# ─── POST /chat/bulk/delete ───


@router.post("/bulk/delete", response_model=BulkDeleteResponse)
async def bulk_delete_chats(
    body: BulkDeleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    count = await conv_service.bulk_delete_threads(
        db, body.thread_ids, user_id=current_user.id
    )
    return BulkDeleteResponse(deleted_count=count)


# ─── POST /chat/bulk/move ───


@router.post("/bulk/move", response_model=BulkMoveResponse)
async def bulk_move_chats(
    body: BulkMoveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    count = await conv_service.move_threads(
        db, body.thread_ids, body.project_id, user_id=current_user.id
    )
    return BulkMoveResponse(moved_count=count, project_id=body.project_id)


# ─── GET /chat/{thread_id} ───


@router.get("/{thread_id}", response_model=ThreadDetail)
async def get_chat(
    thread_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_session),
):
    thread, messages = await conv_service.get_thread(
        db, thread_id, user_id=current_user.id
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    return ThreadDetail(
        id=thread.id,
        project_id=thread.project_id,
        title=thread.title,
        starred=thread.starred,
        messages=[
            MessageOut(
                id=m.id,
                thread_id=m.thread_id,
                conversation_id=m.conversation_id,
                parent_conversation_id=m.parent_conversation_id,
                role=m.role,
                content=m.content,
                reasoning=m.reasoning,
                metadata_=m.metadata_,
                feedback=(
                    {"liked": m._feedback_liked, "comment": m._feedback_comment}
                    if getattr(m, "_feedback_liked", None) is not None
                    else None
                ),
                created_at=m.created_at,
            )
            for m in messages
        ],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


# ─── DELETE /chat/{thread_id} ───


@router.delete("/{thread_id}", response_model=DeleteResponse)
async def delete_chat(
    thread_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await conv_service.delete_thread(
        db, thread_id, user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")
    return DeleteResponse(deleted=True, thread_id=thread_id)


# ─── PATCH /chat/{thread_id}/star ───


@router.patch("/{thread_id}/star")
async def star_chat(
    thread_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    starred = await conv_service.star_thread(
        db, thread_id, user_id=current_user.id
    )
    if starred is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread_id": thread_id, "starred": starred}


# ─── PATCH /chat/{thread_id}/rename ───


@router.patch("/{thread_id}/rename")
async def rename_chat(
    thread_id: uuid.UUID,
    body: RenameRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    ok = await conv_service.rename_thread(
        db, thread_id, body.title, user_id=current_user.id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread_id": thread_id, "title": body.title}


# ─── PATCH /chat/{thread_id}/move ───


@router.patch("/{thread_id}/move")
async def move_chat(
    thread_id: uuid.UUID,
    body: MoveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    count = await conv_service.move_threads(
        db, [thread_id], body.project_id, user_id=current_user.id
    )
    if count == 0:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {
        "thread_id": thread_id,
        "project_id": str(body.project_id) if body.project_id else None,
    }


# ─── POST /chat/{thread_id}/ask ───


@router.post("/{thread_id}/ask")
@limiter.limit(f"{settings.RATE_LIMIT_ASK_PER_MINUTE}/minute")
async def ask_question(
    request: Request,
    thread_id: uuid.UUID,
    body: AskRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    request_time = time.perf_counter()
    # Register the cancel event BEFORE any DB work so stop works immediately
    # even if the user presses stop during the pre-SSE query phase.
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    conversation_id = body.conversation_id or uuid.uuid4()

    user_meta = None
    if body.source_conversation_id:
        user_meta = {"source_conversation_id": str(body.source_conversation_id)}
        # prior_sql is only present on "Refine this query" actions.
        # Flag the user message so the UI can distinguish refinements from
        # regular follow-ups (which also carry source_conversation_id).
        if body.prior_sql:
            user_meta["is_refinement"] = True

    # Single round-trip: the thread UPDATE inside save_message_and_touch
    # filters by user_id, so it doubles as the ownership check. No separate
    # thread_exists query needed — saves ~1.3s of pre-SSE DB latency.
    _t1 = time.perf_counter()
    save_result = await conv_service.save_message_and_touch(
        db,
        thread_id=thread_id,
        conversation_id=conversation_id,
        role="user",
        content=body.question,
        auto_title=body.question[:200],
        metadata=user_meta,
        user_id=current_user.id,
    )
    if save_result is None:
        _active_streams.pop(str(thread_id), None)
        raise HTTPException(status_code=404, detail="Thread not found")
    _, is_first_message = save_result
    await db.commit()
    _t_save = (time.perf_counter() - _t1) * 1000

    if is_first_message:
        title = conv_service.make_title(body.question)
        if title:
            asyncio.create_task(conv_service.save_smart_title(thread_id, title))

    logger.info(
        f"[ask] pre-SSE timing for thread {thread_id}: "
        f"save_user_msg={_t_save:.0f}ms, "
        f"total_handler={(time.perf_counter() - request_time) * 1000:.0f}ms"
    )

    generator = _build_sse_generator(
        question=body.question,
        thread_id=thread_id,
        conversation_id=conversation_id,
        parent_conversation_id=None,
        generate_title=is_first_message,
        user_id=str(current_user.id),
        request_time=request_time,
        persona=_TONE_TO_PERSONA.get(body.response_tone, ""),
        max_rows=body.max_rows,
        deep_analysis=body.deep_analysis,
        cancel_event=_cancel_ev,
        prior_sql=body.prior_sql or "",
        user_display_name=_get_display_name(current_user),
        user_email=current_user.email,
    )
    return EventSourceResponse(generator(), ping=15)


# ─── POST /chat/{thread_id}/retry ───


@router.post("/{thread_id}/retry")
@limiter.limit(f"{settings.RATE_LIMIT_ASK_PER_MINUTE}/minute")
async def retry_response(
    request: Request,
    thread_id: uuid.UUID,
    body: RetryRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    request_time = time.perf_counter()
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    question, existing_parent = await conv_service.get_question_and_parent(
        db, body.conversation_id
    )
    if not question:
        _active_streams.pop(str(thread_id), None)
        raise HTTPException(status_code=404, detail="Original conversation not found")

    new_conversation_id = uuid.uuid4()
    root = existing_parent or body.conversation_id

    user_meta = None
    if body.source_conversation_id:
        user_meta = {"source_conversation_id": str(body.source_conversation_id)}

    save_result = await conv_service.save_message_and_touch(
        db,
        thread_id=thread_id,
        conversation_id=new_conversation_id,
        parent_conversation_id=root,
        role="user",
        content=question,
        metadata=user_meta,
        user_id=current_user.id,
    )
    if save_result is None:
        _active_streams.pop(str(thread_id), None)
        raise HTTPException(status_code=404, detail="Thread not found")
    await db.commit()

    generator = _build_sse_generator(
        question=question,
        thread_id=thread_id,
        conversation_id=new_conversation_id,
        parent_conversation_id=root,
        user_id=str(current_user.id),
        request_time=request_time,
        persona=_TONE_TO_PERSONA.get(body.response_tone, ""),
        max_rows=body.max_rows,
        deep_analysis=body.deep_analysis,
        cancel_event=_cancel_ev,
        user_display_name=_get_display_name(current_user),
        user_email=current_user.email,
    )
    return EventSourceResponse(generator(), ping=15)


# ─── POST /chat/{thread_id}/edit ───


@router.post("/{thread_id}/edit")
@limiter.limit(f"{settings.RATE_LIMIT_ASK_PER_MINUTE}/minute")
async def edit_question(
    request: Request,
    thread_id: uuid.UUID,
    body: EditRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    request_time = time.perf_counter()
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    original, existing_parent = await conv_service.get_question_and_parent(
        db, body.conversation_id
    )
    if original is None:
        _active_streams.pop(str(thread_id), None)
        raise HTTPException(status_code=404, detail="Original conversation not found")

    regen_title = await conv_service.is_first_conversation(
        db, thread_id, body.conversation_id
    )

    new_conversation_id = uuid.uuid4()
    root = existing_parent or body.conversation_id

    user_meta = None
    if body.source_conversation_id:
        user_meta = {"source_conversation_id": str(body.source_conversation_id)}

    save_result = await conv_service.save_message_and_touch(
        db,
        thread_id=thread_id,
        conversation_id=new_conversation_id,
        parent_conversation_id=root,
        role="user",
        content=body.question,
        metadata=user_meta,
        user_id=current_user.id,
    )
    if save_result is None:
        _active_streams.pop(str(thread_id), None)
        raise HTTPException(status_code=404, detail="Thread not found")

    await db.commit()

    generator = _build_sse_generator(
        question=body.question,
        thread_id=thread_id,
        conversation_id=new_conversation_id,
        parent_conversation_id=root,
        generate_title=regen_title,
        user_id=str(current_user.id),
        request_time=request_time,
        persona=_TONE_TO_PERSONA.get(body.response_tone, ""),
        max_rows=body.max_rows,
        deep_analysis=body.deep_analysis,
        cancel_event=_cancel_ev,
        user_display_name=_get_display_name(current_user),
        user_email=current_user.email,
    )
    return EventSourceResponse(generator(), ping=15)


# ─── POST /chat/{thread_id}/stop ───


@router.post("/{thread_id}/stop")
async def stop_generation(
    thread_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    cancelled = cancel_stream(str(thread_id))
    return {"thread_id": thread_id, "stopped": cancelled}


# ─── POST /chat/{thread_id}/conversations/{conversation_id}/feedback ───


@router.post(
    "/{thread_id}/conversations/{conversation_id}/feedback",
    response_model=FeedbackOut,
    status_code=200,
)
async def submit_feedback(
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: FeedbackRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        feedback, langfuse_trace_id = await fb_service.save_feedback(
            db,
            conversation_id=conversation_id,
            thread_id=thread_id,
            liked=body.liked,
            comment=body.comment,
        )
    except Exception:
        logger.exception("Feedback save failed")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    # Forward the thumbs-up/down score to Langfuse so it appears on the trace.
    # Runs in a background task so it never blocks the API response.
    if langfuse_trace_id:
        from app.core.langfuse_integration import score_trace

        asyncio.get_running_loop().run_in_executor(
            None,
            lambda: score_trace(
                trace_id=langfuse_trace_id,
                name="user-feedback",
                value=1.0 if body.liked else 0.0,
                comment=body.comment or None,
                data_type="BOOLEAN",
            ),
        )

    return FeedbackOut(
        id=feedback.id,
        conversation_id=conversation_id,
        liked=feedback.liked,
        comment=feedback.comment,
        created_at=feedback.created_at,
    )

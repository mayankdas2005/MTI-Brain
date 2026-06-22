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
from app.db import async_session_factory, get_async_session, get_read_session
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
from app.services.agents.nodes.audit import anti_pattern_merge_key
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


# Rolling thread-context window: how many recent analytics turns of structured context
# (anchors/filters/measures/dimensions — never SQL) to carry forward to the specialists.
_PRIOR_CONTEXT_WINDOW = 4


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
    prior_question: str = "",
    user_display_name: str = "",
    user_email: str | None = None,
    is_retry: bool = False,
    prior_execution_context: dict | None = None,
    prior_context_window: list[dict] | None = None,
    conversation_history: str = "(no prior context)",
    global_instructions: str = "",
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
                "chart_type": save_data.get("chart_type"),
                "alternative_chart_specs": save_data.get("alternative_chart_specs", []),
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
                "graph_context": save_data.get("graph_context"),
                "neo4j_raw_graph": save_data.get("neo4j_raw_graph"),
                "confidence": save_data.get("confidence"),
                "pattern_id": save_data.get("pattern_id"),
                "tables_used": ",".join(save_data.get("tables_used") or []),
                "intent": save_data.get("intent") or "",
                "complexity": save_data.get("complexity") or "",
                "query_intent": save_data.get("query_intent") or [],
                "entity_tokens": save_data.get("entity_tokens") or [],
                "search_terms": save_data.get("search_terms") or [],
                "is_followup": save_data.get("is_followup", False),
                "sample_rows": save_data.get("sample_rows", []),
                "query_col_stats": save_data.get("query_col_stats", []),
                "was_truncated": save_data.get("was_truncated", False),
                "true_total_rows": save_data.get("true_total_rows"),
                "preference_summary": save_data.get("preference_summary"),
                "sensitivity_table":  save_data.get("sensitivity_table"),
                "denominator_context": save_data.get("denominator_context"),
                "temporal_projection": save_data.get("temporal_projection"),
                "deep_analysis":      save_data.get("deep_analysis", False),
                "tribal_facts":       save_data.get("tribal_facts") or [],
                "prior_execution_context": save_data.get("prior_execution_context"),
            }),
        )
        _conf = save_data.get("confidence")
        if _conf:
            logger.debug(
                "chat | confidence saved | score={} | label={}",
                _conf.get("score"), _conf.get("label"),
            )
        try:
            async with async_session_factory() as save_db:
                await conv_service.save_message(save_db, **msg_kwargs)
                await conv_service.touch_thread(save_db, thread_id)
                await save_db.commit()
        except Exception:
            logger.exception("Failed to save assistant message")

    async def event_generator():
        from app.services.agents.pipeline import stream_pipeline

        _stream_start = request_time or time.perf_counter()
        _cancel = cancel_event if cancel_event is not None else asyncio.Event()
        _active_streams[str(thread_id)] = _cancel

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

            # Accumulate partial state so stop/error saves a complete picture,
            # not just whatever answer tokens happened to stream before the cut.
            _partial_answer: list[str] = []
            _partial_reasoning: dict[str, list[str]] = {}   # node -> [token, ...]
            _partial_reasoning_order: list[str] = []        # insertion order
            _partial_steps: list[dict] = []                 # pipeline timeline
            _partial_steps_idx: dict[str, int] = {}         # node -> last step index
            _partial_node_labels: dict[str, str] = {}       # node -> display label

            def _build_partial_save(answer_override: str | None = None) -> dict:
                _dur = int((time.perf_counter() - _stream_start) * 1000)
                _ans = answer_override if answer_override is not None else "".join(_partial_answer)
                _rsn = [
                    {
                        "node": n,
                        "label": _partial_node_labels.get(n, n),
                        "text": "".join(_partial_reasoning.get(n, [])).strip(),
                    }
                    for n in _partial_reasoning_order
                    if "".join(_partial_reasoning.get(n, [])).strip()
                ]
                return {
                    "answer": _ans,
                    "stopped": True,
                    "duration_ms": _dur,
                    "reasoning": _rsn,
                    "pipeline_steps": list(_partial_steps),
                }

            async for sse_ev in stream_pipeline(
                question=question,
                thread_id=str(thread_id),
                persona=persona,
                user_id=user_id,
                max_rows=max_rows,
                deep_analysis=deep_analysis,
                cancel_event=_cancel,
                prior_sql=prior_sql,
                prior_question=prior_question,
                user_email=user_email,
                user_display_name=user_display_name,
                is_retry=is_retry,
                prior_execution_context=prior_execution_context,
                prior_context_window=prior_context_window,
                conversation_history=conversation_history,
                global_instructions=global_instructions,
            ):
                event_name = sse_ev["event"]
                data = sse_ev["data"]

                # ── Accumulate partial state ──────────────────────────────────
                if event_name == "node.start":
                    _n = data["node"]
                    _partial_node_labels[_n] = data.get("message", _n)
                    _partial_steps_idx[_n] = len(_partial_steps)
                    _partial_steps.append({
                        "node": _n,
                        "message": _partial_node_labels[_n],
                        "is_retry": data.get("is_retry", False),
                        "status": "active",
                        "duration_ms": 0,
                        "total_tokens": 0,
                        "reasoning": "",
                    })
                elif event_name == "node.done":
                    _n = data["node"]
                    _i = _partial_steps_idx.get(_n)
                    if _i is not None:
                        _partial_steps[_i].update({
                            "status": data.get("status", "done"),
                            "duration_ms": data.get("duration_ms", 0),
                            "total_tokens": data.get("total_tokens", 0),
                            "reasoning": "".join(_partial_reasoning.get(_n, [])).strip(),
                        })
                elif event_name == "reasoning.delta":
                    _n = data["node"]
                    if _n not in _partial_reasoning:
                        _partial_reasoning[_n] = []
                        _partial_reasoning_order.append(_n)
                    _partial_reasoning[_n].append(data.get("text", ""))
                elif event_name == "answer.delta":
                    _partial_answer.append(data.get("text", ""))

                # ── Terminal events ───────────────────────────────────────────
                # Await the DB commit BEFORE yielding the terminal event so
                # any fetchThread call triggered by the frontend sees the
                # saved message immediately (no blank-thread race condition).
                if event_name == "stopped":
                    _saved = _build_partial_save()
                    data = {**data, "conversation_id": str(conversation_id), "duration_ms": _saved["duration_ms"]}
                    await _save_assistant_message(_saved)
                    yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}
                    break

                if event_name == "done":
                    data = {**data, "conversation_id": str(conversation_id)}
                    await _save_assistant_message(data)
                    yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}
                    break

                yield {"event": event_name, "data": json.dumps(data, default=_json_serial)}

                if event_name == "error":
                    _partial = "".join(_partial_answer)
                    await _save_assistant_message(
                        _build_partial_save(_partial or data.get("message", "Something went wrong. Please try again."))
                    )
                    break

        except Exception as e:
            logger.exception(f"Stream error for thread {thread_id}: {e}")
            asyncio.create_task(_save_assistant_message(
                _build_partial_save("Something went wrong. Please try again.")
            ))
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "Something went wrong while processing your question.",
                    "conversation_id": str(conversation_id),
                }),
            }

        except BaseException:
            # GeneratorExit (client disconnect / navigation). Save whatever
            # partial state was accumulated so the thread is not left empty.
            asyncio.create_task(_save_assistant_message(
                _build_partial_save()
            ))
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
):
    request_time = time.perf_counter()
    # Cancel any pipeline already running for this thread, then register a fresh event.
    # The sleep(0) yields to the event loop so the old pipeline's cancel propagates
    # (CancelledError into its in-flight LangGraph task) before new DB work starts —
    # reduces SQLAlchemy pool cleanup races that log CancelledError/TimeoutError.
    cancel_stream(str(thread_id))
    await asyncio.sleep(0)
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    conversation_id = body.conversation_id or uuid.uuid4()

    prior_question = ""
    user_meta = None
    if body.source_conversation_id:
        user_meta = {"source_conversation_id": str(body.source_conversation_id)}
        # prior_sql is only present on "Refine this query" actions.
        # Flag the user message so the UI can distinguish refinements from
        # regular follow-ups (which also carry source_conversation_id).
        if body.prior_sql:
            user_meta["is_refinement"] = True

    # Use a manual session block so the DB connection is returned to the pool
    # BEFORE the SSE stream starts. FastAPI's Depends(get_async_session) holds
    # the connection open until the response body is complete — for a long-lived
    # SSE stream that means minutes, which causes asyncpg close(timeout=2)
    # timeouts when the pool tries to recycle the idle connection mid-stream.
    _t1 = time.perf_counter()
    is_first_message = False
    async with async_session_factory() as db:
        # ── Resolve prior_question for refinements (before save) ────────────
        # Lookup happens first so prior_question can be stored in user_meta and
        # persisted in the message JSONB metadata — this is how N-level chained
        # refinements always trace back to the original question:
        #   q2r1 meta: {prior_question: q2.content}
        #   q2r2 meta: {prior_question: q2.content}  ← reads from q2r1 meta
        #   q2rN meta: {prior_question: q2.content}  ← always the original
        if body.source_conversation_id and body.prior_sql:
            try:
                from sqlalchemy import select as _select
                from app.models.conversation import MTIBrainMessage
                _row = await db.execute(
                    _select(MTIBrainMessage.content, MTIBrainMessage.metadata_)
                    .where(MTIBrainMessage.conversation_id == body.source_conversation_id)
                    .where(MTIBrainMessage.role == "user")
                    .limit(1)
                )
                _msg = _row.one_or_none()
                if _msg:
                    _msg_content, _msg_meta = _msg
                    # Chained: _msg_meta["prior_question"] already = original question
                    # First-level: _msg_meta absent → fall back to _msg_content (= original question)
                    prior_question = (_msg_meta or {}).get("prior_question") or _msg_content or ""
                if prior_question:
                    user_meta["prior_question"] = prior_question  # stored for future chain links
                    logger.info("[ask] refinement | prior_question resolved | len={}", len(prior_question))
            except Exception:
                logger.warning("[ask] refinement | prior_question lookup failed | conversation_id={}", body.source_conversation_id)

        # ── Load rolling thread-context window from recent analytics assistant messages ──
        prior_execution_context = None
        prior_context_window: list[dict] = []
        try:
            async with db.begin_nested():
                from sqlalchemy import select as _select
                from app.models.conversation import MTIBrainMessage
                _prior_rows = await db.execute(
                    _select(MTIBrainMessage.metadata_)
                    .where(MTIBrainMessage.thread_id == thread_id)
                    .where(MTIBrainMessage.role == "assistant")
                    .where(MTIBrainMessage.metadata_["question_type"].astext == "analytics")
                    .order_by(MTIBrainMessage.created_at.desc())
                    .limit(_PRIOR_CONTEXT_WINDOW)
                )
                for _meta in _prior_rows.scalars().all():
                    _pec = (_meta or {}).get("prior_execution_context")
                    if _pec:
                        prior_context_window.append(_pec)
                if prior_context_window:
                    prior_execution_context = prior_context_window[0]
        except Exception:
            logger.debug("[ask] prior context window lookup failed (non-fatal)")

        # ── Load conversation history from DB (persistent, not Redis) ──
        conversation_history = "(no prior context)"
        try:
            conversation_history = await conv_service.get_conversation_history(
                db, thread_id, before_conversation_id=None
            )
        except Exception:
            logger.debug("[ask] conversation_history lookup failed (non-fatal)")

        # ── Load enabled standing instructions for this user ──
        global_instructions = ""
        try:
            from app.services.chat.instructions import load_enabled_instructions, format_instructions
            _instructions = await load_enabled_instructions(db, current_user.id)
            global_instructions = format_instructions(_instructions)
        except Exception:
            logger.debug("[ask] global_instructions lookup failed (non-fatal)")

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

    # Connection returned to pool here — before SSE starts
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
        persona=_TONE_TO_PERSONA.get(body.response_tone, "analyst"),
        max_rows=body.max_rows,
        deep_analysis=body.deep_analysis,
        cancel_event=_cancel_ev,
        prior_sql=body.prior_sql or "",
        prior_question=prior_question,
        user_display_name=_get_display_name(current_user),
        user_email=current_user.email,
        prior_execution_context=prior_execution_context,
        prior_context_window=prior_context_window,
        conversation_history=conversation_history,
        global_instructions=global_instructions,
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
):
    request_time = time.perf_counter()
    cancel_stream(str(thread_id))
    await asyncio.sleep(0)
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    new_conversation_id = uuid.uuid4()
    question: str = ""
    root: uuid.UUID | None = None

    prior_sql = ""
    prior_question = ""

    async with async_session_factory() as db:
        from sqlalchemy import select as _select
        from app.models.conversation import MTIBrainMessage
        _row = await db.execute(
            _select(
                MTIBrainMessage.content,
                MTIBrainMessage.parent_conversation_id,
                MTIBrainMessage.metadata_,
            )
            .where(MTIBrainMessage.conversation_id == body.conversation_id)
            .where(MTIBrainMessage.role == "user")
            .limit(1)
        )
        _msg = _row.one_or_none()
        if not _msg:
            _active_streams.pop(str(thread_id), None)
            raise HTTPException(status_code=404, detail="Original conversation not found")
        question, existing_parent, orig_meta = _msg
        orig_meta = orig_meta or {}
        root = existing_parent or body.conversation_id

        is_refinement_retry = bool(orig_meta.get("is_refinement"))
        user_meta: dict | None = None

        if is_refinement_retry:
            prior_question = orig_meta.get("prior_question") or ""
            source_cid_str = orig_meta.get("source_conversation_id") or ""
            if source_cid_str:
                try:
                    _asst = await db.execute(
                        _select(MTIBrainMessage.metadata_)
                        .where(MTIBrainMessage.conversation_id == uuid.UUID(source_cid_str))
                        .where(MTIBrainMessage.role == "assistant")
                        .limit(1)
                    )
                    _asst_meta = _asst.scalar_one_or_none() or {}
                    prior_sql = _asst_meta.get("sql") or ""
                except Exception:
                    logger.warning("[retry] refinement | prior_sql lookup failed | source_cid={}", source_cid_str)
            user_meta = {
                "source_conversation_id": source_cid_str,
                "is_refinement": True,
                "prior_question": prior_question,
            }
        elif orig_meta.get("source_conversation_id"):
            user_meta = {"source_conversation_id": str(orig_meta["source_conversation_id"])}

        # ── Load rolling thread-context window for retries (scoped BEFORE the retried msg) ──
        prior_execution_context = None
        prior_context_window: list[dict] = []
        try:
            async with db.begin_nested():
                # Get timestamp of the original user message being retried
                _user_ts_row = await db.execute(
                    _select(MTIBrainMessage.created_at)
                    .where(MTIBrainMessage.conversation_id == body.conversation_id)
                    .where(MTIBrainMessage.role == "user")
                    .limit(1)
                )
                _user_ts = _user_ts_row.scalar_one_or_none()
                if _user_ts:
                    _prior_rows = await db.execute(
                        _select(MTIBrainMessage.metadata_)
                        .where(MTIBrainMessage.thread_id == thread_id)
                        .where(MTIBrainMessage.role == "assistant")
                        .where(MTIBrainMessage.metadata_["question_type"].astext == "analytics")
                        .where(MTIBrainMessage.created_at < _user_ts)
                        .order_by(MTIBrainMessage.created_at.desc())
                        .limit(_PRIOR_CONTEXT_WINDOW)
                    )
                    for _meta in _prior_rows.scalars().all():
                        _pec = (_meta or {}).get("prior_execution_context")
                        if _pec:
                            prior_context_window.append(_pec)
                    if prior_context_window:
                        prior_execution_context = prior_context_window[0]
        except Exception:
            pass

        # ── Load conversation history from DB (before the retried conversation) ──
        conversation_history = "(no prior context)"
        try:
            conversation_history = await conv_service.get_conversation_history(
                db, thread_id, before_conversation_id=body.conversation_id
            )
        except Exception:
            pass

        # ── Load enabled standing instructions for this user ──
        global_instructions = ""
        try:
            from app.services.chat.instructions import load_enabled_instructions, format_instructions
            _instructions = await load_enabled_instructions(db, current_user.id)
            global_instructions = format_instructions(_instructions)
        except Exception:
            logger.debug("[retry] global_instructions lookup failed (non-fatal)")

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
    # Connection returned to pool before SSE starts

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
        prior_sql=prior_sql,
        prior_question=prior_question,
        user_display_name=_get_display_name(current_user),
        user_email=current_user.email,
        is_retry=not is_refinement_retry,
        prior_execution_context=prior_execution_context,
        prior_context_window=prior_context_window,
        conversation_history=conversation_history,
        global_instructions=global_instructions,
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
):
    request_time = time.perf_counter()
    cancel_stream(str(thread_id))
    await asyncio.sleep(0)
    _cancel_ev = asyncio.Event()
    _active_streams[str(thread_id)] = _cancel_ev

    new_conversation_id = uuid.uuid4()
    regen_title = False
    root: uuid.UUID | None = None

    async with async_session_factory() as db:
        original, existing_parent = await conv_service.get_question_and_parent(
            db, body.conversation_id
        )
        if original is None:
            _active_streams.pop(str(thread_id), None)
            raise HTTPException(status_code=404, detail="Original conversation not found")

        regen_title = await conv_service.is_first_conversation(
            db, thread_id, body.conversation_id
        )
        root = existing_parent or body.conversation_id

        user_meta = None
        if body.source_conversation_id:
            user_meta = {"source_conversation_id": str(body.source_conversation_id)}

        # ── Load rolling thread-context window for edits (scoped BEFORE the edited msg) ──
        # Advisory only: the edited question may differ entirely, so the specialists re-ground
        # from the new text; this window just fills elliptical references.
        prior_execution_context = None
        prior_context_window: list[dict] = []
        try:
            async with db.begin_nested():
                from sqlalchemy import select as _select
                from app.models.conversation import MTIBrainMessage
                # Get timestamp of the original user message being edited
                _user_ts_row = await db.execute(
                    _select(MTIBrainMessage.created_at)
                    .where(MTIBrainMessage.conversation_id == body.conversation_id)
                    .where(MTIBrainMessage.role == "user")
                    .limit(1)
                )
                _user_ts = _user_ts_row.scalar_one_or_none()
                if _user_ts:
                    _prior_rows = await db.execute(
                        _select(MTIBrainMessage.metadata_)
                        .where(MTIBrainMessage.thread_id == thread_id)
                        .where(MTIBrainMessage.role == "assistant")
                        .where(MTIBrainMessage.metadata_["question_type"].astext == "analytics")
                        .where(MTIBrainMessage.created_at < _user_ts)
                        .order_by(MTIBrainMessage.created_at.desc())
                        .limit(_PRIOR_CONTEXT_WINDOW)
                    )
                    for _meta in _prior_rows.scalars().all():
                        _pec = (_meta or {}).get("prior_execution_context")
                        if _pec:
                            prior_context_window.append(_pec)
                    if prior_context_window:
                        prior_execution_context = prior_context_window[0]
        except Exception:
            pass

        # ── Load conversation history from DB (before the edited conversation) ──
        conversation_history = "(no prior context)"
        try:
            conversation_history = await conv_service.get_conversation_history(
                db, thread_id, before_conversation_id=body.conversation_id
            )
        except Exception:
            pass

        # ── Load enabled standing instructions for this user ──
        global_instructions = ""
        try:
            from app.services.chat.instructions import load_enabled_instructions, format_instructions
            _instructions = await load_enabled_instructions(db, current_user.id)
            global_instructions = format_instructions(_instructions)
        except Exception:
            logger.debug("[edit] global_instructions lookup failed (non-fatal)")

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
    # Connection returned to pool before SSE starts

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
        is_retry=True,
        prior_execution_context=prior_execution_context,
        prior_context_window=prior_context_window,
        conversation_history=conversation_history,
        global_instructions=global_instructions,
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
        feedback, langfuse_trace_id, pattern_id, neo4j_context = await fb_service.save_feedback(
            db,
            conversation_id=conversation_id,
            thread_id=thread_id,
            liked=body.liked,
            comment=body.comment,
        )
    except Exception:
        logger.exception("Feedback save failed")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    # Neo4j feedback loops — run in thread pool (sync Neo4j writes)
    if pattern_id or neo4j_context:
        from app.services.agents import neo4j_client
        _liked = body.liked
        _comment = body.comment

        def _neo4j_feedback_update() -> None:
            try:
                # Loop 2a: update existing QueryPattern counts + promote on like
                if pattern_id:
                    neo4j_client.update_pattern_feedback(pattern_id, _liked)
                    if _liked:
                        neo4j_client.promote_pattern_to_template(pattern_id)

                if neo4j_context:
                    action = neo4j_context["action"]

                    # Loop 2b: dislike → write :AntiPattern (any confidence level)
                    if action == "dislike":
                        _ap_error_summary = (_comment or "User rated response negatively")[:300]
                        _ap_tables = neo4j_context["tables_used"] or ""
                        _ap_intent = neo4j_context["intent"] or ""
                        neo4j_client.write_anti_pattern({
                            "id": str(uuid.uuid4()),
                            "merge_key": anti_pattern_merge_key("user_dislike", _ap_intent, _ap_tables, _ap_error_summary),
                            "question_text": (neo4j_context["question"] or "")[:500],
                            "sql_fragment": (neo4j_context["sql"] or "")[:500],
                            "error_type": "user_dislike",
                            "error_summary": _ap_error_summary,
                            "failing_element": "",
                            "tables_involved": _ap_tables,
                            "intent": _ap_intent,
                            "complexity": neo4j_context["complexity"] or "",
                            "cohere_embedding": neo4j_context["embedding"],
                        })
                        logger.info(
                            "chat | AntiPattern written from dislike | pattern_id={} | intent={}",
                            pattern_id, neo4j_context.get("intent"),
                        )

                    # Loop 2c: low-confidence like → retroactively write :QueryPattern
                    elif action == "like_without_pattern":
                        _tables = [
                            t.strip()
                            for t in (neo4j_context["tables_used"] or "").split(",")
                            if t.strip()
                        ]
                        neo4j_client.write_query_pattern({
                            "id": str(uuid.uuid4()),
                            "question_text": (neo4j_context["question"] or "")[:500],
                            "sql_cte_outline": "",
                            "join_outline": "",
                            "filter_summary": "",
                            "tables_used": _tables,
                            "intent": neo4j_context["intent"] or "",
                            "complexity": neo4j_context["complexity"] or "simple",
                            "recompile_count": 0,
                            "repair_count": 0,
                            "confidence_score": neo4j_context["confidence_score"],
                            "row_count": None,
                            "user_id": "",
                            "cohere_embedding": neo4j_context["embedding"],
                            "promotion_status": "active",
                            "liked_count": 1,
                            "disliked_count": 0,
                        })
                        logger.info(
                            "chat | QueryPattern written from low-confidence like | confidence={} | intent={}",
                            neo4j_context.get("confidence_score"), neo4j_context.get("intent"),
                        )

            except Exception as _e:
                logger.warning("chat | neo4j feedback update failed | pattern_id={} | error={}", pattern_id, _e)

        asyncio.get_running_loop().run_in_executor(None, _neo4j_feedback_update)
        logger.debug("chat | neo4j feedback queued | pattern_id={} | liked={}", pattern_id, body.liked)

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

"""Chat API endpoints for thread management and (mocked) Q&A streaming."""

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

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
from app.services.analysis.sql import extract_source_tables
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


def _mock_response_payload(question: str, conversation_id: uuid.UUID) -> dict:
    answer = (
        f"This is a mock response for: \"{question[:120]}\". "
        "The text-to-SQL pipeline is disabled in this build."
    )
    columns = ["account", "balance_usd"]
    rows = [
        ["Operating", 4_250_000],
        ["Payroll", 1_875_000],
        ["Reserve", 6_500_000],
        ["FX Hedge", 920_000],
        ["Liquidity Buffer", 3_100_000],
    ]
    sql = (
        "SELECT account, balance_usd\n"
        "FROM treasury_accounts\n"
        "WHERE snapshot_date = CURRENT_DATE - INTERVAL '1 day'\n"
        "ORDER BY balance_usd DESC;"
    )
    # Trust-strip fields. `source_tables` is real (sqlglot on the SQL above).
    # `data_freshness_at` is mocked at "two hours ago" until the Snowflake
    # integration ships a real value (e.g. INFORMATION_SCHEMA.TABLES.LAST_ALTERED).
    # Metric metadata stays None until the metric catalog backend lands.
    source_tables = extract_source_tables(sql, dialect="snowflake")
    data_freshness_at = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    return {
        "answer": answer,
        "sql": sql,
        "intent": "cash_position",
        "resolved_filters": "",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "chart_spec": {
            "type": "bar",
            "title": "Cash Balance by Account (Yesterday)",
            "x_key": "account",
            "y_keys": ["balance_usd"],
            "y_label": "USD",
            "data": [
                {"account": row[0], "balance_usd": row[1]}
                for row in rows
            ],
        },
        "follow_ups": [
            "Show me a breakdown by account type",
            "How does this compare to last week?",
        ],
        "schema_fqn": "",
        "run_id": str(uuid.uuid4()),
        "stopped": False,
        "needs_clarification": False,
        "duration_ms": 50,
        "langfuse_trace_id": None,
        "conversation_id": str(conversation_id),
        # Trust-strip provenance — see comment above.
        "source_tables": source_tables,
        "data_freshness_at": data_freshness_at,
        "metric_name": None,
        "metric_owner": None,
        "metric_defined_at": None,
    }


def _build_sse_generator(
    question: str,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    parent_conversation_id: uuid.UUID | None,
    generate_title: bool = False,
    user_id: str | None = None,
    request_time: float | None = None,
    deep_analysis: bool = False,
    cancel_event: asyncio.Event | None = None,
):
    async def _save_assistant_message(save_data: dict) -> None:
        from app.db import async_session_factory

        msg_kwargs = dict(
            thread_id=thread_id,
            conversation_id=conversation_id,
            parent_conversation_id=parent_conversation_id,
            role="assistant",
            content=save_data.get("answer", ""),
            reasoning=save_data.get("reasoning"),
            metadata={
                "sql": save_data.get("sql", ""),
                "intent": save_data.get("intent", ""),
                "resolved_filters": save_data.get("resolved_filters", ""),
                "columns": save_data.get("columns", []),
                "rows": save_data.get("rows", []),
                "row_count": save_data.get("row_count", 0),
                "chart_spec": save_data.get("chart_spec"),
                "follow_ups": save_data.get("follow_ups", []),
                "run_id": save_data.get("run_id", ""),
                "stopped": save_data.get("stopped", False),
                "duration_ms": save_data.get("duration_ms"),
                # Authoritative pipeline timeline. Replaces extractSteps
                # heuristics on the client - each entry has node, message,
                # status, started_at_ms, duration_ms, and per-step reasoning.
                "pipeline_steps": save_data.get("pipeline_steps") or [],
                # Trust-strip provenance. source_tables comes from sqlglot
                # on the executed SQL; data_freshness_at + metric_* come
                # from Snowflake/catalog integrations once they ship.
                "source_tables": save_data.get("source_tables") or [],
                "data_freshness_at": save_data.get("data_freshness_at"),
                "metric_name": save_data.get("metric_name"),
                "metric_owner": save_data.get("metric_owner"),
                "metric_defined_at": save_data.get("metric_defined_at"),
            },
        )
        try:
            async with async_session_factory() as save_db:
                await conv_service.save_message(save_db, **msg_kwargs)
                await conv_service.touch_thread(save_db, thread_id)
                await save_db.commit()
        except Exception:
            logger.exception("Failed to save assistant message")

    async def event_generator():
        _stream_start = request_time or time.perf_counter()
        # Use the pre-registered event (created before DB work so stop works
        # immediately), or fall back to a new one for safety.
        _cancel = cancel_event if cancel_event is not None else asyncio.Event()
        _active_streams[str(thread_id)] = _cancel
        final_data = _mock_response_payload(question, conversation_id)
        _reasoning_parts: list[str] = []

        # Track what was actually delivered to the client. On stop, only the
        # delivered content is saved to the DB — not the pre-computed full payload.
        _streamed_answer: list[str] = []
        _data_delivered = False    # execute.done was yielded
        _chart_delivered = False   # chart was yielded
        _followups_delivered = False  # follow_ups was yielded

        _steps: list[dict] = []
        _current_step: dict | None = None

        def _now_ms() -> int:
            return int((time.perf_counter() - _stream_start) * 1000)

        def _begin_step(node: str, message: str) -> dict:
            nonlocal _current_step
            now = _now_ms()
            if _current_step is not None:
                _current_step["status"] = "done"
                _current_step["duration_ms"] = max(0, now - _current_step["started_at_ms"])
            step = {
                "node": node,
                "message": message,
                "status": "active",
                "started_at_ms": now,
                "duration_ms": None,
                "reasoning": "",
            }
            _steps.append(step)
            _current_step = step
            return step

        def _close_steps() -> None:
            nonlocal _current_step
            if _current_step is not None:
                now = _now_ms()
                _current_step["status"] = "done"
                _current_step["duration_ms"] = max(0, now - _current_step["started_at_ms"])
                _current_step = None

        # Sentinel raised at every cancel-check point so we exit the entire
        # pipeline immediately regardless of nesting depth.
        class _Stopped(BaseException):
            pass

        def _check() -> None:
            if _cancel.is_set():
                raise _Stopped()

        async def _patch_duration(conv_id: uuid.UUID, duration: int) -> None:
            try:
                from app.db import async_session_factory
                from app.models.conversation import MTIBrainMessage
                from sqlalchemy import cast, update
                from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

                async with async_session_factory() as patch_db:
                    stmt = (
                        update(MTIBrainMessage)
                        .where(
                            MTIBrainMessage.conversation_id == conv_id,
                            MTIBrainMessage.role == "assistant",
                        )
                        .values(
                            metadata_=MTIBrainMessage.metadata_.op("||")(
                                cast({"duration_ms": duration}, PG_JSONB)
                            )
                        )
                    )
                    await patch_db.execute(stmt)
                    await patch_db.commit()
            except Exception:
                logger.exception("Failed to patch duration_ms")

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

            # ── Classify ──────────────────────────────────────────────────
            _check()
            step = _begin_step("classify", "Classifying question")
            yield {"event": "node.start", "data": json.dumps({"node": step["node"], "message": step["message"], "started_at_ms": step["started_at_ms"]})}
            yield {"event": "reasoning.pending", "data": json.dumps({"node": "classify"})}
            for _chunk in [
                "**Reading the question**\n\n",
                "Parsing intent from natural language input. ",
                "Question references financial data - likely a treasury or cash management query.\n\n",
                "**Routing decision**\n\n",
                "Matches pattern: balance enquiry across accounts. ",
                "Classifying as `data_query` → will route to SQL generation.\n",
            ]:
                _check()
                if _current_step is not None:
                    _current_step["reasoning"] += _chunk
                yield {"event": "reasoning.delta", "data": json.dumps({"node": "classify", "text": _chunk})}
                await asyncio.sleep(0.05)
            _check()
            yield {"event": "classify", "data": json.dumps({"question_type": "data_query"})}

            # ── Generate SQL ──────────────────────────────────────────────
            _check()
            step = _begin_step("generate_sql", "Building SQL query")
            yield {"event": "node.start", "data": json.dumps({"node": step["node"], "message": step["message"], "started_at_ms": step["started_at_ms"]})}
            yield {"event": "reasoning.pending", "data": json.dumps({"node": "generate_sql"})}
            for _chunk in [
                "**Analyzing question and building query**\n\n",
                "The question asks for total cash balance across all bank accounts as of yesterday. ",
                "I need to query the `treasury_accounts` table and filter by `snapshot_date = CURRENT_DATE - 1`.\n\n",
                "Identifying relevant columns: `account`, `balance_usd`, `snapshot_date`.\n\n",
                "**Resolving entities**\n\n",
                "Mapping 'bank accounts' → `treasury_accounts` table. ",
                "Mapping 'yesterday' → `snapshot_date = CURRENT_DATE - INTERVAL '1 day'`.\n\n",
                "No FX conversion needed - all balances stored in USD.\n\n",
                "**Validating results**\n\n",
                "Ordering by `balance_usd DESC` to surface largest positions first. ",
                "Query looks correct - returning all 5 accounts with end-of-day balances.\n",
            ]:
                _check()
                _reasoning_parts.append(_chunk)
                if _current_step is not None:
                    _current_step["reasoning"] += _chunk
                yield {"event": "reasoning.delta", "data": json.dumps({"node": "generate_sql", "text": _chunk})}
                await asyncio.sleep(0.06)
            _check()
            yield {"event": "generate_sql", "data": json.dumps({"sql": final_data["sql"], "intent": final_data["intent"]})}

            # ── Execute ───────────────────────────────────────────────────
            _check()
            step = _begin_step("execute", "Executing query")
            yield {"event": "node.start", "data": json.dumps({"node": step["node"], "message": step["message"], "started_at_ms": step["started_at_ms"]})}
            yield {"event": "reasoning.pending", "data": json.dumps({"node": "execute"})}
            for _chunk in [
                "**Running query against data warehouse**\n\n",
                "Submitting SQL to execution engine. ",
                f"Retrieved {final_data['row_count']} row{'s' if final_data['row_count'] != 1 else ''}.\n\n",
                "**Validating output**\n\n",
                "No null values in key columns. ",
                "Row count within expected range - no anomalies detected.\n",
            ]:
                _check()
                if _current_step is not None:
                    _current_step["reasoning"] += _chunk
                yield {"event": "reasoning.delta", "data": json.dumps({"node": "execute", "text": _chunk})}
                await asyncio.sleep(0.05)
            _check()
            yield {
                "event": "execute.done",
                "data": json.dumps({
                    "sql": final_data["sql"],
                    "columns": final_data["columns"],
                    "rows": final_data["rows"],
                    "row_count": final_data["row_count"],
                    "source_tables": final_data.get("source_tables") or [],
                    "data_freshness_at": final_data.get("data_freshness_at"),
                    "metric_name": final_data.get("metric_name"),
                    "metric_owner": final_data.get("metric_owner"),
                    "metric_defined_at": final_data.get("metric_defined_at"),
                }),
            }
            _data_delivered = True
            _check()
            if final_data.get("chart_spec"):
                yield {"event": "chart", "data": json.dumps({"spec": final_data["chart_spec"]})}
                _chart_delivered = True

            # ── Respond ───────────────────────────────────────────────────
            _check()
            step = _begin_step("respond", "Preparing answer")
            yield {"event": "node.start", "data": json.dumps({"node": step["node"], "message": step["message"], "started_at_ms": step["started_at_ms"]})}
            yield {"event": "reasoning.pending", "data": json.dumps({"node": "respond"})}
            for _chunk in [
                "**Composing response**\n\n",
                "Structuring query results into a readable answer. ",
                "Selecting appropriate chart type based on data shape.\n\n",
                "**Generating follow-up suggestions**\n\n",
                "Identifying logical next questions based on the result set. ",
                "Follow-up chips ready.\n",
            ]:
                _check()
                if _current_step is not None:
                    _current_step["reasoning"] += _chunk
                yield {"event": "reasoning.delta", "data": json.dumps({"node": "respond", "text": _chunk})}
                await asyncio.sleep(0.05)

            for chunk in final_data["answer"].split(" "):
                _check()
                yield {"event": "answer.delta", "data": json.dumps({"text": chunk + " "})}
                _streamed_answer.append(chunk + " ")
                await asyncio.sleep(0.02)

            _check()
            yield {"event": "follow_ups", "data": json.dumps({"questions": final_data["follow_ups"]})}
            _followups_delivered = True

            # ── Done ──────────────────────────────────────────────────────
            _close_steps()
            final_data["reasoning"] = "".join(_reasoning_parts)
            final_data["pipeline_steps"] = _steps
            final_data["duration_ms"] = int((time.perf_counter() - _stream_start) * 1000)
            logger.info(f"[sse] stream done for thread {thread_id}: total_duration={final_data['duration_ms']}ms")
            yield {"event": "done", "data": json.dumps(final_data)}
            asyncio.create_task(_save_assistant_message(final_data))
            asyncio.create_task(_patch_duration(conversation_id, final_data["duration_ms"]))

        except _Stopped:
            _close_steps()
            final_data["stopped"] = True
            final_data["duration_ms"] = int((time.perf_counter() - _stream_start) * 1000)
            # Trim final_data to only what was actually delivered to the client.
            # The pre-computed payload contains the full response; if stop fired
            # before certain sections were yielded, erase them so the DB record
            # matches what the user actually saw.
            final_data["answer"] = "".join(_streamed_answer)
            if not _data_delivered:
                final_data["sql"] = None
                final_data["columns"] = []
                final_data["rows"] = []
                final_data["row_count"] = 0
                final_data["source_tables"] = []
                final_data["chart_spec"] = None
                final_data["follow_ups"] = []
            elif not _chart_delivered:
                final_data["chart_spec"] = None
            if not _followups_delivered:
                final_data["follow_ups"] = []
            logger.info(f"[sse] stream stopped by user for thread {thread_id}")
            yield {"event": "stopped", "data": json.dumps({
                "conversation_id": str(conversation_id),
                "duration_ms": final_data["duration_ms"],
                "pipeline_steps": _steps,
            })}
            asyncio.create_task(_save_assistant_message(final_data))

        except Exception as e:
            logger.exception(f"Mock stream error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "Something went wrong while processing your question.",
                    "conversation_id": str(conversation_id),
                }),
            }

        except BaseException:
            # GeneratorExit (client navigated away / network drop) or other
            # non-Exception BaseException. Save whatever was streamed so the
            # DB is not left with a dangling unsaved record.
            _close_steps()
            final_data["stopped"] = True
            final_data["duration_ms"] = int((time.perf_counter() - _stream_start) * 1000)
            asyncio.create_task(_save_assistant_message(final_data))
            raise  # re-raise so sse_starlette cleans up correctly
        finally:
            # Identity check: only remove OUR event. A subsequent ask/retry/edit
            # may have already registered a new cancel_event for the same thread.
            # A plain pop() would silently delete the NEW stream's ability to stop.
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
        deep_analysis=body.deep_analysis,
        cancel_event=_cancel_ev,
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
        cancel_event=_cancel_ev,
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
        cancel_event=_cancel_ev,
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
    status_code=201,
)
async def submit_feedback(
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: FeedbackRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        feedback = await fb_service.save_feedback(
            db,
            conversation_id=conversation_id,
            thread_id=thread_id,
            liked=body.liked,
            comment=body.comment,
        )
    except Exception:
        logger.exception("Feedback save failed")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    return FeedbackOut(
        id=feedback.id,
        conversation_id=conversation_id,
        liked=feedback.liked,
        comment=feedback.comment,
        created_at=feedback.created_at,
    )

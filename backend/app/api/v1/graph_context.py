"""Graph Context API — generate, retrieve, and delete per-conversation graph visualizations.

Architecture:
  POST   /graph-context/generate/{conversation_id}  → queues background generation; returns 202
  GET    /graph-context/{conversation_id}            → returns status + presigned URL when ready
  DELETE /graph-context/{conversation_id}            → removes from S3 + DB
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.logger import logger
from app.db.session import async_session_factory, async_read_session_factory
from app.models.conversation import MTIBrainGraphContext
from app.services.graph_context_builder import (
    generate_and_store,
    delete_from_s3,
    generate_presigned_url,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update

router = APIRouter()


# ── Response schema ────────────────────────────────────────────────────────────

class GraphContextStatusOut(BaseModel):
    status: str
    message: str
    url: str | None = None


# ── Background task ────────────────────────────────────────────────────────────

async def _mark_status(
    conversation_id: uuid.UUID,
    status: str,
    s3_key: str = "",
    s3_url: str = "",
    error_msg: str = "",
) -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(MTIBrainGraphContext)
                .where(MTIBrainGraphContext.conversation_id == conversation_id)
                .values(
                    status=status,
                    s3_key=s3_key,
                    s3_url=s3_url,
                    error_msg=error_msg[:500] if error_msg else None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        logger.info("[graph_context] DB → {} | conv={}", status, conversation_id)
    except Exception as db_exc:
        logger.error("[graph_context] ORM update failed ({}) | conv={}", db_exc, conversation_id)
        try:
            from sqlalchemy import text as sa_text
            async with async_session_factory() as session:
                await session.execute(
                    sa_text(
                        "UPDATE mti_brain_graph_context SET status=:s, updated_at=now() "
                        "WHERE conversation_id=:cid"
                    ),
                    {"s": status, "cid": str(conversation_id)},
                )
                await session.commit()
        except Exception as raw_exc:
            logger.error("[graph_context] raw SQL also failed | conv={} | {}", conversation_id, raw_exc)


async def _run_generate(conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
    logger.info("[graph_context] background task STARTED | conv={}", conversation_id)
    try:
        s3_key, s3_url = await generate_and_store(conversation_id, user_id)
        await _mark_status(conversation_id, "ready", s3_key=s3_key, s3_url=s3_url)
        logger.info("[graph_context] READY | conv={} | url={}", conversation_id, s3_url)
    except Exception as exc:
        logger.exception("[graph_context] FAILED | conv={} | error={}", conversation_id, exc)
        await _mark_status(conversation_id, "failed", error_msg=str(exc))


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/generate/{conversation_id}",
    response_model=GraphContextStatusOut,
    status_code=202,
)
async def generate_graph_context(
    conversation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Queue graph context generation for a conversation.

    - If a ready graph context already exists → returns it immediately (cache hit).
    - If generation is already in flight (<120s) → returns pending status.
    - Otherwise → inserts/resets the DB row, queues the background task.
    """
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainGraphContext)
            .where(MTIBrainGraphContext.conversation_id == conversation_id)
        )
        existing = result.scalar_one_or_none()

    if existing and existing.status == "ready":
        return GraphContextStatusOut(
            status="ready",
            message="Graph context is ready.",
            url=existing.s3_url or None,
        )

    if existing and existing.status == "pending":
        age = datetime.now(timezone.utc) - existing.created_at.replace(tzinfo=timezone.utc)
        if age < timedelta(seconds=120):
            return GraphContextStatusOut(
                status="pending",
                message="Graph context generation is already in progress.",
            )

    try:
        async with async_session_factory() as session:
            if existing:
                await session.execute(
                    update(MTIBrainGraphContext)
                    .where(MTIBrainGraphContext.conversation_id == conversation_id)
                    .values(status="pending", s3_key="", s3_url="", error_msg=None,
                            updated_at=datetime.now(timezone.utc))
                )
            else:
                from app.models.conversation import MTIBrainMessage
                from sqlalchemy import select as sa_select
                msg_result = await session.execute(
                    sa_select(MTIBrainMessage.thread_id)
                    .where(MTIBrainMessage.conversation_id == conversation_id)
                    .limit(1)
                )
                thread_id = msg_result.scalar_one_or_none()
                if not thread_id:
                    raise HTTPException(status_code=404, detail="Conversation not found.")

                session.add(MTIBrainGraphContext(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    user_id=current_user.id,
                    status="pending",
                ))
            await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("graph_context: failed to upsert DB row: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to queue graph context generation.")

    background_tasks.add_task(_run_generate, conversation_id, current_user.id)
    logger.info("[graph_context] queued | conv={} | user={}", conversation_id, current_user.id)
    return GraphContextStatusOut(
        status="pending",
        message="Graph context generation has started. Check back in a moment.",
    )


@router.get("/{conversation_id}/download")
async def download_graph_context(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stream the graph context HTML from S3 as an attachment download."""
    import asyncio as _asyncio
    from app.services.graph_context_builder import _get_s3_client
    from app.core.config import settings

    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainGraphContext)
            .where(MTIBrainGraphContext.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row or row.status != "ready" or not row.s3_key:
        raise HTTPException(status_code=404, detail="Graph context not available for download.")

    def _fetch() -> bytes:
        obj = _get_s3_client().get_object(Bucket=settings.AWS_BOTO3_BUCKET_NAME, Key=row.s3_key)
        return obj["Body"].read()

    try:
        data = await _asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as exc:
        logger.error("[graph_context] download fetch failed | key={} | {}", row.s3_key, exc)
        raise HTTPException(status_code=502, detail="Failed to retrieve graph context from storage.")

    filename = row.s3_key.rsplit("/", 1)[-1]
    return StreamingResponse(
        iter([data]),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Filename": filename,
        },
    )


@router.get(
    "/{conversation_id}",
    response_model=GraphContextStatusOut,
)
async def get_graph_context(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the current status and presigned URL for a graph context."""
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainGraphContext)
            .where(MTIBrainGraphContext.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="No graph context found for this conversation.")

    presigned_url: str | None = None
    if row.status == "ready" and row.s3_key:
        try:
            presigned_url = await generate_presigned_url(row.s3_key)
        except Exception as exc:
            logger.error("[graph_context] presign failed | key={} | {}", row.s3_key, exc)

    return GraphContextStatusOut(
        status=row.status,
        message={
            "pending": "Graph context is being generated.",
            "ready":   "Graph context is ready.",
            "failed":  f"Graph context generation failed: {row.error_msg or 'unknown error'}",
        }.get(row.status, row.status),
        url=presigned_url,
    )


@router.delete(
    "/{conversation_id}",
    response_model=GraphContextStatusOut,
)
async def delete_graph_context(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a graph context from S3 and the database."""
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainGraphContext)
            .where(MTIBrainGraphContext.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        return GraphContextStatusOut(status="not_found", message="No graph context found.")

    if row.s3_key:
        await delete_from_s3(row.s3_key)

    async with async_session_factory() as session:
        result = await session.execute(
            select(MTIBrainGraphContext)
            .where(MTIBrainGraphContext.conversation_id == conversation_id)
        )
        to_delete = result.scalar_one_or_none()
        if to_delete:
            await session.delete(to_delete)
            await session.commit()

    return GraphContextStatusOut(status="deleted", message="Graph context deleted successfully.")

"""Dashboard API — generate, retrieve, and delete per-conversation HTML dashboards.

Architecture:
  POST   /dashboard/generate/{conversation_id}  → queues background generation; returns 202
  GET    /dashboard/{conversation_id}           → returns status + S3 URL when ready
  DELETE /dashboard/{conversation_id}           → removes from S3 + DB
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.logger import logger
from app.db.session import async_session_factory, async_read_session_factory
from app.models.conversation import MTIBrainDashboard
from app.services.dashboard_builder import generate_and_store, delete_from_s3, generate_presigned_url
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update

router = APIRouter()


# ── Response schemas ───────────────────────────────────────────────────────────

class DashboardStatusOut(BaseModel):
    status: str
    message: str
    url: str | None = None


# ── Background task ────────────────────────────────────────────────────────────

async def _mark_status(conversation_id: uuid.UUID, status: str, s3_key: str = "",
                       s3_url: str = "", error_msg: str = "") -> None:
    """Robustly update the dashboard row status. Never raises."""
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(MTIBrainDashboard)
                .where(MTIBrainDashboard.conversation_id == conversation_id)
                .values(
                    status=status,
                    s3_key=s3_key,
                    s3_url=s3_url,
                    error_msg=error_msg[:500] if error_msg else None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        logger.info(f"[dashboard] DB → {status} | conv={conversation_id}")
    except Exception as db_exc:
        # Last-resort raw SQL so the row never stays 'pending' forever
        logger.error(f"[dashboard] ORM update failed ({db_exc}), trying raw SQL | conv={conversation_id}")
        try:
            from sqlalchemy import text as sa_text
            async with async_session_factory() as session:
                await session.execute(
                    sa_text(
                        "UPDATE mti_brain_dashboard SET status=:s, updated_at=now() "
                        "WHERE conversation_id=:cid"
                    ),
                    {"s": status, "cid": str(conversation_id)},
                )
                await session.commit()
            logger.info(f"[dashboard] raw SQL update succeeded | conv={conversation_id}")
        except Exception as raw_exc:
            logger.error(f"[dashboard] raw SQL also failed | conv={conversation_id} | {raw_exc}")


async def _run_generate(conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Called by FastAPI BackgroundTasks after the 202 response is sent."""
    logger.info(f"[dashboard] background task STARTED | conv={conversation_id} | user={user_id}")
    try:
        s3_key, s3_url = await generate_and_store(conversation_id, user_id)
        logger.info(f"[dashboard] generation complete, persisting ready state | conv={conversation_id}")
        await _mark_status(conversation_id, "ready", s3_key=s3_key, s3_url=s3_url)
        logger.info(f"[dashboard] READY | conv={conversation_id} | url={s3_url}")

    except Exception as exc:
        logger.exception(f"[dashboard] FAILED | conv={conversation_id} | error={exc}")
        await _mark_status(conversation_id, "failed", error_msg=str(exc))


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/generate/{conversation_id}",
    response_model=DashboardStatusOut,
    status_code=202,
)
async def generate_dashboard(
    conversation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Queue dashboard generation for a conversation.

    - If a ready dashboard already exists → returns it immediately (cache hit).
    - If generation is already in flight (<120s) → returns pending status.
    - Otherwise → inserts/resets the DB row, queues the background task.
    """
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainDashboard)
            .where(MTIBrainDashboard.conversation_id == conversation_id)
        )
        existing = result.scalar_one_or_none()

    # Cache hit — return existing URL immediately
    if existing and existing.status == "ready":
        return DashboardStatusOut(
            status="ready",
            message="Dashboard is ready.",
            url=existing.s3_url or None,
        )

    # De-duplicate in-flight requests (120s window)
    if existing and existing.status == "pending":
        age = datetime.now(timezone.utc) - existing.created_at.replace(tzinfo=timezone.utc)
        if age < timedelta(seconds=120):
            return DashboardStatusOut(
                status="pending",
                message="Dashboard generation is already in progress.",
            )

    # Upsert DB row as pending
    try:
        async with async_session_factory() as session:
            if existing:
                await session.execute(
                    update(MTIBrainDashboard)
                    .where(MTIBrainDashboard.conversation_id == conversation_id)
                    .values(status="pending", s3_key="", s3_url="", error_msg=None,
                            updated_at=datetime.now(timezone.utc))
                )
            else:
                # Look up thread_id from the message
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

                session.add(MTIBrainDashboard(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    user_id=current_user.id,
                    status="pending",
                ))
            await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("dashboard: failed to upsert DB row: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to queue dashboard generation.")

    background_tasks.add_task(_run_generate, conversation_id, current_user.id)

    logger.info(f"[dashboard] queued | conv={conversation_id} | user={current_user.id}")
    return DashboardStatusOut(
        status="pending",
        message="Dashboard generation has started. Check back in a moment.",
    )


@router.get(
    "/{conversation_id}",
    response_model=DashboardStatusOut,
)
async def get_dashboard(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the current status and URL for a dashboard."""
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainDashboard)
            .where(MTIBrainDashboard.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="No dashboard found for this conversation.")

    # Generate a fresh presigned URL for ready dashboards (avoids public bucket requirement)
    presigned_url: str | None = None
    if row.status == "ready" and row.s3_key:
        try:
            presigned_url = await generate_presigned_url(row.s3_key)
        except Exception as exc:
            logger.error(f"[dashboard] presign failed for key={row.s3_key}: {exc}")

    return DashboardStatusOut(
        status=row.status,
        message={
            "pending": "Dashboard is being generated.",
            "ready":   "Dashboard is ready.",
            "failed":  f"Dashboard generation failed: {row.error_msg or 'unknown error'}",
        }.get(row.status, row.status),
        url=presigned_url,
    )


@router.get("/{conversation_id}/download")
async def download_dashboard(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stream the dashboard HTML from S3 as an attachment download.

    Avoids CORS restrictions — the browser calls this same-origin endpoint,
    which fetches from S3 server-side and pipes the bytes back.
    """
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainDashboard)
            .where(MTIBrainDashboard.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row or row.status != "ready" or not row.s3_key:
        raise HTTPException(status_code=404, detail="Dashboard not available for download.")

    from app.services.dashboard_builder import _get_s3_client
    from app.core.config import settings
    import asyncio

    def _fetch() -> bytes:
        client = _get_s3_client()
        obj = client.get_object(Bucket=settings.AWS_BOTO3_BUCKET_NAME, Key=row.s3_key)
        return obj["Body"].read()

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as exc:
        logger.error(f"[dashboard] download fetch failed | key={row.s3_key} | {exc}")
        raise HTTPException(status_code=502, detail="Failed to retrieve dashboard from storage.")

    filename = row.s3_key.rsplit("/", 1)[-1]
    return StreamingResponse(
        iter([data]),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Filename": filename,
        },
    )


@router.delete(
    "/{conversation_id}",
    response_model=DashboardStatusOut,
)
async def delete_dashboard(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a dashboard from S3 and the database."""
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainDashboard)
            .where(MTIBrainDashboard.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        return DashboardStatusOut(status="not_found", message="No dashboard found.")

    if row.s3_key:
        await delete_from_s3(row.s3_key)

    async with async_session_factory() as session:
        result = await session.execute(
            select(MTIBrainDashboard)
            .where(MTIBrainDashboard.conversation_id == conversation_id)
        )
        to_delete = result.scalar_one_or_none()
        if to_delete:
            await session.delete(to_delete)
            await session.commit()

    return DashboardStatusOut(status="deleted", message="Dashboard deleted successfully.")

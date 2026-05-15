"""Dashboard API — generate, retrieve, and delete per-conversation HTML dashboards.

Architecture (final):
  POST   /dashboard/generate/{conversation_id}  → enqueue generation (S3 upload)
  GET    /dashboard/{conversation_id}           → return S3 URL or status
  DELETE /dashboard/{conversation_id}           → remove from S3

Current state: skeleton — endpoints exist, auth is enforced, DB/S3 calls are
stubbed.  Replace the TODO sections with real logic incrementally.
"""

import uuid

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.logger import logger
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ── Response schemas ──────────────────────────────────────────────────────────

class DashboardStatusOut(BaseModel):
    status: str
    message: str
    url: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

async def _run_generate(conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Background task — runs after the 202 response is already sent to the client.

    Full implementation will:
    1. Verify conversation belongs to user_id.
    2. Load question / SQL / rows / knowledge context / answer from DB.
    3. Call the LLM to render an HTML dashboard.
    4. Upload the HTML to S3 at
       s3://<bucket>/dashboards/<user_id>/<conversation_id>.html
    5. Persist the S3 URL to the message's metadata in DB.
    """
    try:
        logger.info(
            "dashboard background generation started",
            extra={"conversation_id": str(conversation_id), "user_id": str(user_id)},
        )
        # TODO: load conversation from DB
        # TODO: call LLM → HTML
        # TODO: upload to S3
        # TODO: persist URL to DB
        logger.info(
            "dashboard background generation complete",
            extra={"conversation_id": str(conversation_id)},
        )
    except Exception as exc:
        logger.exception(
            "dashboard background generation failed for %s: %s", conversation_id, exc
        )


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
    """Accept the generation request and return immediately.

    The actual work (LLM + S3 upload) runs in a background task so this
    endpoint never blocks the client.
    """
    try:
        logger.info(
            "generate_dashboard accepted",
            extra={"conversation_id": str(conversation_id), "user_id": str(current_user.id)},
        )
        background_tasks.add_task(_run_generate, conversation_id, current_user.id)
        return DashboardStatusOut(
            status="accepted",
            message="Dashboard generation started. It will be ready shortly.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("generate_dashboard failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to queue dashboard generation.")


@router.get(
    "/{conversation_id}",
    response_model=DashboardStatusOut,
)
async def get_dashboard(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the S3 URL for a previously generated dashboard.

    Full implementation will:
    1. Look up the S3 URL stored in the message's metadata (or a dedicated
       dashboard table).
    2. Optionally generate a pre-signed URL if the bucket is private.
    3. Return 404 if no dashboard exists yet for this conversation.
    """
    try:
        logger.info(
            "get_dashboard requested",
            extra={"conversation_id": str(conversation_id), "user_id": str(current_user.id)},
        )

        # TODO: query DB for stored dashboard URL
        # TODO: optionally generate pre-signed S3 URL

        # Skeleton — nothing stored yet
        raise HTTPException(
            status_code=404,
            detail="No dashboard found for this conversation.",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_dashboard failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve dashboard.")


@router.delete(
    "/{conversation_id}",
    response_model=DashboardStatusOut,
)
async def delete_dashboard(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete the dashboard from S3 when a conversation is deleted.

    Full implementation will:
    1. Verify ownership.
    2. Delete the object from S3.
    3. Clear the URL from the message's metadata in DB.
    """
    try:
        logger.info(
            "delete_dashboard requested",
            extra={"conversation_id": str(conversation_id), "user_id": str(current_user.id)},
        )

        # TODO: verify conversation belongs to current_user
        # TODO: delete s3://<bucket>/dashboards/<user_id>/<conversation_id>.html
        # TODO: clear dashboard_url from DB

        return DashboardStatusOut(
            status="deleted",
            message="Dashboard deleted successfully.",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete_dashboard failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete dashboard.")

"""Dashboard generation service.

Loads conversation data, calls the LLM, injects the body HTML into the
template, and uploads the result to S3.  Returns (s3_key, s3_url).
"""

from __future__ import annotations

import asyncio
import uuid
from functools import partial
from pathlib import Path

import boto3
from app.core.config import settings
from app.core.logger import logger
from app.db.session import async_read_session_factory
from app.models.conversation import MTIBrainMessage
from backend.app.services.neo4j_analytics.bedrock import _region_from_arn
from backend.app.services.neo4j_analytics.helpers import _build_data_summary
from app.services.dashboard_prompt import DASHBOARD_SYSTEM_PROMPT, build_input_markdown
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

_TEMPLATE_PATH = Path(__file__).resolve().parent / "dashboard_template.html"


def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _sync_s3_upload(key: str, html_bytes: bytes, bucket: str) -> None:
    filename = key.split("/")[-1]   # e.g. "dashboard-c7648399-2026-05-17.html"
    client = _get_s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=html_bytes,
        ContentType="text/html; charset=utf-8",
        ContentDisposition=f'inline; filename="{filename}"',
        CacheControl="no-cache",
    )


def _sync_s3_delete(key: str, bucket: str) -> None:
    client = _get_s3_client()
    client.delete_object(Bucket=bucket, Key=key)


def _sync_presign(key: str, bucket: str, expires_in: int) -> str:
    client = _get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


async def generate_presigned_url(s3_key: str, expires_in: int = 7 * 24 * 3600) -> str:
    """Return a presigned GET URL valid for `expires_in` seconds (default 7 days)."""
    bucket = settings.AWS_BOTO3_BUCKET_NAME
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_sync_presign, s3_key, bucket, expires_in))


async def _upload_to_s3(key: str, html_bytes: bytes) -> str:
    """Upload HTML to S3; returns the public URL."""
    bucket = settings.AWS_BOTO3_BUCKET_NAME
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_sync_s3_upload, key, html_bytes, bucket))
    region = settings.AWS_REGION
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


async def delete_from_s3(key: str) -> None:
    """Best-effort S3 deletion. Logs but does not raise on failure."""
    try:
        bucket = settings.AWS_BOTO3_BUCKET_NAME
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, partial(_sync_s3_delete, key, bucket))
    except Exception as exc:
        logger.warning("S3 delete failed for key=%s: %s", key, exc)


def _extract_main(html: str) -> str:
    """Extract <main …>…</main> from LLM output using plain string ops — no regex.

    The prompt instructs the LLM to output ONLY the main block, so the raw
    response should already start with <main and end with </main>.
    This function is a safety net that strips any accidental preamble/fencing.
    """
    # Strip markdown code fences if the model wrapped its output
    text = html.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]          # drop opening fence line
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]           # drop closing fence line

    start = text.find("<main")
    if start == -1:
        return text                              # nothing to strip — return as-is

    # Find the matching </main> — take the LAST one so nested tags are included
    end = text.rfind("</main>")
    if end == -1:
        return text[start:]                      # no closing tag — return from <main onwards
    return text[start : end + len("</main>")]


def _inject_into_template(body_html: str) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("${htmlContent}", body_html)


async def generate_and_store(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[str, str]:
    """Generate a dashboard for a conversation and upload to S3.

    Returns:
        (s3_key, s3_url)

    Raises:
        ValueError: if no assistant message found for this conversation_id.
        Any boto3 / LLM exception is propagated to the caller.
    """
    # ── 1. Load messages ──
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainMessage)
            .where(MTIBrainMessage.conversation_id == conversation_id)
            .order_by(MTIBrainMessage.created_at)
        )
        messages = result.scalars().all()

    user_msg = next((m for m in messages if m.role == "user"), None)
    asst_msg = next((m for m in messages if m.role == "assistant"), None)

    if not asst_msg:
        raise ValueError(f"No assistant message for conversation_id={conversation_id}")

    meta: dict = asst_msg.metadata_ or {}
    question   = user_msg.content if user_msg else ""
    answer     = asst_msg.content or ""
    columns: list | None = meta.get("columns")
    rows: list | None    = meta.get("rows")
    row_count: int | None = meta.get("row_count")
    chart_spec = meta.get("chart_spec")
    intent     = meta.get("intent")
    follow_ups = meta.get("follow_ups")

    logger.info(f"[dashboard] STEP 1 — messages loaded | conv={conversation_id} | user_msg={'yes' if user_msg else 'no'} | asst_msg=yes | row_count={row_count} | cols={len(columns) if columns else 0}")

    # ── 2. Smart data sampling — avoids context bloat for large result sets ──
    col_stats: str = ""
    sampled_rows: list | None = rows
    if columns and rows:
        logger.info(f"[dashboard] STEP 2 — sampling {len(rows)} rows × {len(columns)} cols")
        col_stats, _null_notes, sampled_rows = _build_data_summary(columns, rows)
        logger.info(f"[dashboard] STEP 2 — sampled {len(sampled_rows)} of {len(rows)} rows")
    else:
        logger.info(f"[dashboard] STEP 2 — no rows/cols to sample (answer-only mode)")

    # ── 3. Build LLM input ──
    logger.info(f"[dashboard] STEP 3 — building input markdown")
    input_md = build_input_markdown(
        question=question,
        answer=answer,
        columns=columns,
        rows=sampled_rows,
        row_count=row_count or (len(rows) if rows else None),
        chart_spec=chart_spec,
        intent=intent,
        follow_ups=follow_ups,
        col_stats=col_stats or None,
    )
    logger.info(f"[dashboard] STEP 3 — input_md length={len(input_md)} chars")

    # ── 4. Call LLM ──
    system_prompt = DASHBOARD_SYSTEM_PROMPT
    human_msg = (
        f"INPUT DATA:\n\n{input_md}\n\n"
        "Generate the dashboard HTML now. "
        "Output ONLY the HTML starting with <main class=\"wrap\"> and ending with </main>. "
        "No explanations, no markdown fences."
    )
    logger.info(
        f"[dashboard] STEP 4 — prompt sizes | "
        f"system={len(system_prompt)} chars | human={len(human_msg)} chars"
    )
    logger.info(f"[dashboard] system_preview={system_prompt[:200].replace(chr(10),' ')!r}")
    logger.info(f"[dashboard] human_preview={human_msg[:400].replace(chr(10),' ')!r}")

    dashboard_llm = ChatBedrock(
        model=settings.AWS_BEDROCK_SONNET_ARN,
        provider="anthropic",
        api_key=settings.AWS_BEARER_TOKEN_BEDROCK or None,
        region=_region_from_arn(settings.AWS_BEDROCK_SONNET_ARN),
        streaming=True,
        model_kwargs={"temperature": 0.0, "max_tokens": 8192},
    )

    logger.info("[dashboard] STEP 4 — awaiting LLM (timeout=180s)")
    try:
        response = await asyncio.wait_for(
            dashboard_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_msg),
            ]),
            timeout=180.0,
        )
    except asyncio.TimeoutError:
        # Caught by _run_generate → marks dashboard as 'failed' → button shows Retry
        raise RuntimeError(
            "LLM timed out after 180s — Bedrock may be under load. "
            f"system={len(system_prompt)} chars, human={len(human_msg)} chars"
        )

    # response.content can be str OR list of content blocks — handle both
    raw_content = response.content
    if isinstance(raw_content, list):
        raw_html = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw_content
        )
    else:
        raw_html = str(raw_content)

    logger.info(f"[dashboard] STEP 4 — LLM responded | raw length={len(raw_html)} chars | preview={raw_html[:200].replace(chr(10),' ')!r}")
    body_html = _extract_main(raw_html)
    logger.info(f"[dashboard] STEP 4 — extracted body | length={len(body_html)} chars | starts_with_main={'<main' in body_html[:20]}")

    # ── 5. Inject into template ──
    logger.info(f"[dashboard] STEP 5 — injecting into HTML template")
    full_html = _inject_into_template(body_html)
    logger.info(f"[dashboard] STEP 5 — full HTML length={len(full_html)} chars")

    # ── 6. Upload to S3 — naming: dashboard-{short_id}-{date}.html ──
    from datetime import date as _date
    short_id  = str(conversation_id)[:8]
    today     = _date.today().isoformat()
    s3_key    = f"dashboards/dashboard-{short_id}-{today}.html"
    logger.info(f"[dashboard] STEP 6 — uploading to S3 | key={s3_key}")
    s3_url    = await _upload_to_s3(s3_key, full_html.encode("utf-8"))
    logger.info(f"[dashboard] STEP 6 — S3 upload complete | url={s3_url}")
    return s3_key, s3_url

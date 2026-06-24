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
from bs4 import BeautifulSoup
from app.core.config import settings
from app.core.logger import logger
from app.db.session import async_read_session_factory
from app.models.conversation import MTIBrainMessage
from app.services.agents.bedrock import _region_from_arn
from app.services.agents.helpers import _build_data_summary
from app.services.dashboard_prompt import DASHBOARD_SYSTEM_PROMPT, build_input_markdown
from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select

_TEMPLATE_PATH = Path(__file__).resolve().parent / "dashboard_template.html"
_DASHBOARD_PREFILL = '<main class="wrap">'


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


def _extract_main(html: str, prefill_opening: str | None = None) -> str:
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
        if not prefill_opening:
            return text
        content = text
        if content.endswith("</main>"):
            content = content[: -len("</main>")]
        return f"{prefill_opening}{content}</main>"

    # Find the matching </main> — take the LAST one so nested tags are included
    end = text.rfind("</main>")
    if end == -1:
        return text[start:]                      # no closing tag — return from <main onwards
    return text[start : end + len("</main>")]


def _repair_html(html: str) -> str:
    """Parse with html5lib to auto-close any unclosed tags left by the LLM."""
    soup = BeautifulSoup(html, "html5lib")
    # Strip any <script> tags the LLM may have injected (XSS hardening)
    for tag in soup.find_all("script"):
        tag.decompose()
    main = soup.find("main")
    if main:
        return str(main)
    return html


def _parse_dashboard_number(text: str) -> float | None:
    """Parse a formatted dashboard number into a float.

    Handles: $10.22B, $768.1M, ($3.67B), −$1.56B, +$9.45B, $1,234.56,
    80%, 22×, plain numbers.  Returns None for non-numeric text.
    """
    s = text.strip()
    if not s or s == "—":
        return None

    # Strip multiplier/unit suffixes we don't handle
    if s.endswith("×"):
        return None

    neg = False
    # Parenthesized negatives: ($3.67B)
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True

    # Leading sign
    if s.startswith("−") or s.startswith("-"):
        s = s[1:]
        neg = True
    elif s.startswith("+"):
        s = s[1:]

    # Strip currency symbol
    s = s.lstrip("$").strip()

    # Percentage
    if s.endswith("%"):
        try:
            return float(s[:-1].replace(",", "")) / 100.0
        except ValueError:
            return None

    # Magnitude suffixes
    multiplier = 1.0
    if s.upper().endswith("B"):
        multiplier = 1e9
        s = s[:-1]
    elif s.upper().endswith("M"):
        multiplier = 1e6
        s = s[:-1]
    elif s.upper().endswith("K"):
        multiplier = 1e3
        s = s[:-1]

    s = s.replace(",", "").strip()
    if not s:
        return None

    try:
        val = float(s) * multiplier
        return -val if neg else val
    except ValueError:
        return None


def _build_source_value_set(
    columns: list[str], rows: list[list],
) -> set[float]:
    """Collect all numeric values from source data + per-column aggregates.

    Returns a set of floats for tolerance-based lookup.
    """
    values: set[float] = set()
    col_numerics: dict[int, list[float]] = {}

    for row in rows:
        for ci, val in enumerate(row):
            if val is None:
                continue
            try:
                f = float(val)
                values.add(f)
                col_numerics.setdefault(ci, []).append(f)
            except (TypeError, ValueError):
                pass

    # Per-column aggregates
    for ci, nums in col_numerics.items():
        if nums:
            values.add(sum(nums))
            values.add(min(nums))
            values.add(max(nums))
            values.add(sum(nums) / len(nums))
            values.add(float(len(nums)))

    return values


def _value_matches(parsed: float, source_set: set[float]) -> bool:
    """Check if a parsed value approximately matches any source value."""
    if parsed == 0:
        return 0.0 in source_set
    for sv in source_set:
        if sv == 0:
            continue
        if abs(parsed - sv) / max(abs(sv), 1.0) < 0.02:
            return True
    return False


def _verify_table_values(
    html: str, columns: list[str], rows: list[list],
) -> tuple[str, dict]:
    """Cross-check numeric values in HTML tables against source data.

    Returns (possibly-modified html, stats dict).
    """
    source_set = _build_source_value_set(columns, rows)
    soup = BeautifulSoup(html, "html5lib")

    verified = 0
    unverified = 0

    for table in soup.find_all("table"):
        for section in (table.find("tbody"), table.find("tfoot")):
            if not section:
                continue
            for td in section.find_all("td"):
                text = td.get_text(strip=True)
                parsed = _parse_dashboard_number(text)
                if parsed is None:
                    continue
                if _value_matches(parsed, source_set):
                    verified += 1
                else:
                    unverified += 1

    total = verified + unverified
    stats = {"verified": verified, "unverified": unverified, "total": total}

    if total == 0:
        stats["rate"] = 1.0
        return html, stats

    rate = verified / total
    stats["rate"] = round(rate, 3)

    # Find <main> to inject badge before </main>
    main_tag = soup.find("main")
    if main_tag:
        from bs4 import NavigableString, Tag
        badge_tag = soup.new_tag("p")
        badge_tag["class"] = ["data-source"]
        if rate >= 0.7:
            badge_tag.string = f"\u2713 {verified} of {total} numeric values verified against source data"
        else:
            badge_tag.string = (
                f"{unverified} of {total} numeric values are derived or computed beyond source data"
            )
        main_tag.append(badge_tag)
        # Re-extract <main> as string
        return str(main_tag), stats

    return html, stats


def _inject_into_template(body_html: str, conversation_id: uuid.UUID | None = None) -> str:
    from datetime import date as _date
    today = _date.today()
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    meta = f"<!-- Generated by MTI Brain AI \u00b7 conversation:{conversation_id} \u00b7 {today.isoformat()} -->\n"
    html = template.replace("${htmlContent}", meta + body_html)
    html = html.replace("${generatedDate}", today.strftime("%b %d, %Y"))
    return html


def _format_query_col_stats(
    query_col_stats: list[dict],
    was_truncated: bool = False,
    true_total_rows: int | None = None,
) -> str:
    """Format ColumnStat dicts (from pipeline done event) into the same one-line-per-column
    format produced by _build_data_summary, so dashboard_prompt sees identical structure
    regardless of whether true stats are available.
    """
    lines: list[str] = []
    if was_truncated and true_total_rows:
        lines.append(
            f"Stats source: full Redshift aggregate over {true_total_rows:,} rows (exact)"
        )
    for c in query_col_stats:
        name      = c.get("name") or ""
        dtype     = c.get("dtype") or "unknown"
        distinct  = c.get("distinct_count")
        mn        = c.get("min")
        mx        = c.get("max")
        mean      = c.get("mean")
        null_c    = c.get("null_count")
        total_c   = c.get("total_count")
        top_vals  = c.get("top_values") or []

        null_suffix = ""
        if null_c is not None and total_c:
            null_suffix = f" | null={null_c}/{total_c}"

        if dtype == "numeric":
            parts = []
            if mn is not None:
                parts.append(f"min={mn:g}" if isinstance(mn, float) else f"min={mn}")
            if mx is not None:
                parts.append(f"max={mx:g}" if isinstance(mx, float) else f"max={mx}")
            if mean is not None:
                parts.append(f"mean={round(float(mean), 4):g}")
            if distinct is not None:
                parts.append(f"distinct={distinct}")
            lines.append(f"{name} [numeric]: {' '.join(parts)}{null_suffix}")
        elif dtype == "date":
            range_str = f"{mn} → {mx}" if mn and mx else "(no range)"
            dist_str  = f"   Distinct: {distinct} periods" if distinct is not None else ""
            lines.append(f"{name} [date]: {range_str}{dist_str}{null_suffix}")
        else:
            dist_str = f"distinct={distinct}" if distinct is not None else ""
            top_str  = ", ".join(f'"{v}"({n})' for v, n in top_vals[:5]) if top_vals else ""
            stat_parts = [x for x in [dist_str, f"top=[{top_str}]" if top_str else ""] if x]
            lines.append(f"{name} [string]: {' | '.join(stat_parts)}{null_suffix}")

    return "\n".join(lines)


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
    intent     = meta.get("intent")
    query_intent: list = meta.get("query_intent") or []
    follow_ups = meta.get("follow_ups")

    # New truncation / true-stats metadata (added by pipeline.py + chat.py)
    sample_rows_raw: list   = meta.get("sample_rows") or []
    query_col_stats: list   = meta.get("query_col_stats") or []
    was_truncated: bool     = bool(meta.get("was_truncated", False))
    true_total_rows: int | None = meta.get("true_total_rows")

    # Deep analysis enrichment (only present when deep_analysis=True was used)
    sensitivity_table: list | None   = meta.get("sensitivity_table") or None
    denominator_context: dict | None = meta.get("denominator_context") or None
    temporal_projection: dict | None = meta.get("temporal_projection") or None
    tribal_facts: list | None        = meta.get("tribal_facts") or None

    logger.info(
        f"[dashboard] STEP 1 — messages loaded | conv={conversation_id} "
        f"| user_msg={'yes' if user_msg else 'no'} | asst_msg=yes "
        f"| row_count={row_count} | cols={len(columns) if columns else 0} "
        f"| was_truncated={was_truncated} | true_total_rows={true_total_rows} "
        f"| sample_rows={len(sample_rows_raw)} | query_col_stats={len(query_col_stats)} "
        f"| tribal_facts={len(tribal_facts) if tribal_facts else 0}"
    )

    # ── 2. Smart data sampling — avoids context bloat for large result sets ──
    col_stats: str = ""
    sampled_rows: list | None = rows

    if columns and rows:
        if query_col_stats:
            # True col stats from the full Redshift aggregate — more accurate than pandas on 100 rows
            col_stats = _format_query_col_stats(query_col_stats, was_truncated, true_total_rows)
            logger.info(
                f"[dashboard] STEP 2 — using true col stats | was_truncated={was_truncated} "
                f"| true_total_rows={true_total_rows}"
            )
            # Use smart sample rows (from result_summarizer._smart_sample) when available,
            # else fall back to spread-sample of capped rows
            if sample_rows_raw:
                # sample_rows_raw is list[list] — convert to list[dict] for _build_data_summary compat
                sampled_rows = sample_rows_raw
                logger.info(f"[dashboard] STEP 2 — using smart sample | rows={len(sampled_rows)}")
            else:
                _, _null_notes, sampled_rows = _build_data_summary(columns, rows)
                logger.info(f"[dashboard] STEP 2 — fallback spread sample | rows={len(sampled_rows)}")
        else:
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
        intent=intent,
        query_intent=query_intent,
        follow_ups=follow_ups,
        col_stats=col_stats or None,
        was_truncated=was_truncated,
        true_total_rows=true_total_rows,
        sensitivity_table=sensitivity_table,
        denominator_context=denominator_context,
        temporal_projection=temporal_projection,
        tribal_facts=tribal_facts,
    )
    logger.info(f"[dashboard] STEP 3 — input_md length={len(input_md)} chars")

    # ── 4. Call LLM ──
    system_prompt = DASHBOARD_SYSTEM_PROMPT
    human_msg = (
        f"INPUT DATA:\n\n{input_md}\n\n"
        "Continue the dashboard HTML from the provided opening tag. "
        "Do not repeat the opening <main class=\"wrap\"> tag. "
        "Output only the remaining body content and close with </main>. "
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
        model_kwargs={"temperature": 0.0, "max_tokens": 16384},
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
    body_html = _extract_main(raw_html, prefill_opening=_DASHBOARD_PREFILL)
    body_html = _repair_html(body_html)
    logger.info(f"[dashboard] STEP 4 — extracted body | length={len(body_html)} chars | starts_with_main={'<main' in body_html[:20]}")

    # ── 4b. Verify table values against source data ──
    if columns and rows:
        body_html, verify_stats = _verify_table_values(body_html, columns, rows)
        logger.info(
            f"[dashboard] STEP 4b — verification | "
            f"verified={verify_stats['verified']} unverified={verify_stats['unverified']} "
            f"total={verify_stats['total']} rate={verify_stats.get('rate', 'n/a')}"
        )

    # ── 5. Inject into template ──
    logger.info(f"[dashboard] STEP 5 — injecting into HTML template")
    full_html = _inject_into_template(body_html, conversation_id=conversation_id)
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

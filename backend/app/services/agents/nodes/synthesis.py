"""Node 4: synthesis — two-phase answer generation.

Phase 1 (Haiku):  INSIGHT_EXTRACTOR_PROMPT — reads raw data, extracts structured insights JSON.
Phase 2 (Sonnet): SYNTHESIS_PROMPT         — writes persona-formatted answer from insights only.

Sonnet never sees the raw data, eliminating a whole class of hallucination where the model
invents observations that aren't in the result set.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import datetime
import json
import re

from app.core.logger import logger
from app.services.agents.helpers import _build_data_profile, parse_tag
from app.services.agents.prompts import (
    REASONING_DIRECTIVE_NORMAL, REASONING_DIRECTIVE_DEEP,
    SYNTHESIS_PROMPT, INSIGHT_EXTRACTOR_PROMPT,
)
from app.services.agents.state import AnalyticsState

_FLAG_INSTRUCTIONS = {
    "unexpected_row_count": (
        "Note: This query returned more rows than expected for a KPI metric. "
        "Results may aggregate across multiple matching records. Mention this uncertainty."
    ),
    "trend_insufficient_data": (
        "Note: Only 1 data point was returned. You cannot draw a trend from a single point. "
        "Say so clearly to the user."
    ),
    "low_confidence_filter": (
        "Note: One or more filter values were matched approximately (not exact match). "
        "Results may include slightly different data than intended. Mention the matched values."
    ),
    "time_filter_relaxed": (
        "Note: The time filter was relaxed because the original date range returned no data. "
        "Results span a broader period than requested — state this explicitly."
    ),
    "filters_relaxed": (
        "Note: All WHERE filters were removed because the original query returned 0 rows. "
        "Results are unfiltered and may be broader than the user intended — state this clearly."
    ),
    "high_null_ratio": (
        "Note: A significant portion of result values are NULL. "
        "The data may be incomplete for this dimension — caveat your answer accordingly."
    ),
    "repair_required": (
        "Note: Treat all figures as directional — cross-validate key balances against source records "
        "before escalating or acting on this data."
    ),
    "limit_without_order": (
        "Note: The result set was limited but rows were not explicitly ordered. "
        "The specific rows shown may vary on each execution."
    ),
}


async def synthesis(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("synthesis START | thread={} | persona={} | no_data={}", state["thread_id"], state.get("persona"), state.get("no_data"))

    query_summary = state.get("query_summary") or {}
    no_data = state.get("no_data", False)
    reliability_flags = state.get("reliability_flags") or []
    low_confidence_filters = state.get("low_confidence_filters") or []
    zero_row_probe_result = (state.get("zero_row_probe_result") or "") if no_data else ""
    ir_list = state.get("semantic_ir_list") or []
    anchor_tables = ir_list[0].get("anchor_tables", []) if ir_list else []

    result_list = state.get("result_list") or []
    total_rows_received = sum(len(r.get("rows") or []) for r in result_list)

    # Collect flat column list and all rows (same pattern as chart_agent)
    all_columns: list[str] = (
        [c["name"] for c in query_summary["columns"]]
        if query_summary.get("columns")
        else []
    )
    all_rows: list[list] = []
    for res in result_list:
        if res.get("rows"):
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            all_rows.extend(res["rows"])

    logger.info(
        "synthesis | inputs | thread={} | no_data={} | total_rows={} | anchor_tables={} | query_summary_keys={} | zero_row_probe={}",
        state["thread_id"], no_data, total_rows_received, anchor_tables,
        list(query_summary.keys()), (zero_row_probe_result or "")[:120],
    )

    flag_instructions = "\n".join(
        _FLAG_INSTRUCTIONS.get(flag, "") for flag in reliability_flags if flag in _FLAG_INSTRUCTIONS
    )

    # Surface SQL quality signals — synthesis should know if the answer required
    # multiple attempts so it can calibrate its confidence caveats accordingly.
    repair_count = state.get("repair_count", 0)
    recompile_count = state.get("recompile_count", 0)
    quality_context_lines = []
    if repair_count:
        # Do NOT expose "SQL repair" to users — it's an internal pipeline detail.
        # The "repair_required" flag in reliability_flags already triggers the correct caveat
        # via _FLAG_INSTRUCTIONS. Only flag it — do not add a separate quality_context line.
        if "repair_required" not in reliability_flags:
            reliability_flags = list(reliability_flags) + ["repair_required"]
    if recompile_count:
        # Recompile is also internal. No user-facing line needed.
        pass
    # Inject pre-computed data quality check from data_quality_checker node.
    # When data_quality_flag=True, synthesis opens with ### Data Quality Concern
    # (enforced by the prompt DATA_INTEGRITY_GATE section + quality_context).
    data_quality_flag = state.get("data_quality_flag", False)
    data_quality_reason = state.get("data_quality_reason")
    if data_quality_flag and data_quality_reason:
        quality_context_lines.insert(0, f"DATA QUALITY CONCERN DETECTED: {data_quality_reason}")
        quality_context_lines.insert(1, "Your answer MUST open with ### Data Quality Concern before any other section.")
        if "repair_required" not in reliability_flags:
            reliability_flags = list(reliability_flags) + ["data_quality_concern"]

    quality_context = "\n".join(quality_context_lines)

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    semantic_context = state.get("semantic_context") or {}
    session_summary = semantic_context.get("session_summary") or state.get("summary") or ""
    is_followup = semantic_context.get("is_followup", False)
    feedback_context = state.get("feedback_context") or ""
    memory_context = semantic_context.get("memory_context") or ""

    if session_summary:
        followup_note = " This is a follow-up — open by connecting to the prior finding before presenting new data." if is_followup else ""
        conversation_section = f"CONVERSATION CONTEXT:{followup_note}\n<conversation_context>{session_summary}</conversation_context>"
    else:
        conversation_section = ""

    feedback_section = (
        f"USER PREFERENCES (past feedback — apply silently):\n<feedback_context>{feedback_context}</feedback_context>"
        if feedback_context else ""
    )
    memory_section = (
        f"USER MEMORY (preferences from prior sessions — apply silently):\n<memory_context>{memory_context}</memory_context>"
        if memory_context else ""
    )

    # Build structured data profile (shared builder with chart_agent)
    data_profile = _build_data_profile(all_columns, all_rows, query_summary)

    current_date_context = _build_current_date_context(
        state.get("current_date") or datetime.date.today().isoformat(),
        all_columns,
        all_rows,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from app.core.retry import retry_async

    # ── Phase 1: Insight Extraction (Haiku — fast, data-facing) ──────────────
    # Haiku reads the raw data and produces a structured insights JSON.
    # This is the only phase that sees the raw data profile.

    extractor_prompt = INSIGHT_EXTRACTOR_PROMPT.format_messages(
        question=state["question"],
        current_date_context=current_date_context,
        flag_instructions_text=flag_instructions or "",
        quality_context=quality_context,
        no_data="YES" if no_data else "NO",
        zero_row_probe_result=zero_row_probe_result,
        data_profile=data_profile,
    )

    haiku = get_llm("fast")
    insights_json: str = "{}"

    try:
        @llm_breaker
        async def _extract():
            return await retry_async(
                lambda: haiku.ainvoke(extractor_prompt),
                service="bedrock-insight-extractor",
                max_attempts=2,
                backoff_base=3.0,
            )
        ext_response = await _extract()
        raw_insights = ext_response.content or ""
        parsed_tag = parse_tag(raw_insights, "insights") or ""
        # Validate it's parseable JSON; fall back to raw if not
        import json_repair
        parsed = json_repair.loads(parsed_tag or raw_insights)
        if isinstance(parsed, dict):
            insights_json = json.dumps(parsed, indent=2)
            logger.info(
                "synthesis | insight extraction OK | thread={} | depth={} | findings={}",
                state["thread_id"],
                parsed.get("depth", "?"),
                len(parsed.get("findings") or []),
            )
        else:
            logger.warning("synthesis | insight extraction returned non-dict | thread={}", state["thread_id"])
    except Exception as e:
        logger.warning("synthesis | insight extraction failed, using empty insights | thread={} | error={}", state["thread_id"], e)

    # ── Phase 2: Answer Writing (Sonnet — quality, insight-facing) ───────────
    # Sonnet writes the answer from the structured insights only.
    # It never sees the raw data — hallucination from raw data is structurally impossible.

    execution_error = state.get("execution_error") or ""
    no_data_context = ""
    if no_data and execution_error:
        no_data_context = (
            "SQL GENERATION ERROR: The pipeline produced invalid SQL that could not be repaired. "
            f"Technical error: {execution_error[:300]}. "
            "This is an internal SQL generation failure — do NOT advise the user to check source "
            "systems, data engineering, or data quality. Tell the user the system encountered a "
            "technical issue generating the SQL for this query and they should rephrase or retry."
        )
    elif no_data and zero_row_probe_result:
        no_data_context = f"No data returned. Reason: {zero_row_probe_result}"
    elif no_data:
        no_data_context = "No data returned."

    writer_prompt = SYNTHESIS_PROMPT.format_messages(
        persona=state.get("persona", "executive"),
        question=state["question"],
        no_data_context=no_data_context,
        insights_json=insights_json,
        reasoning_directive=reasoning_directive,
        conversation_section=conversation_section,
        memory_section=memory_section,
        feedback_section=feedback_section,
    )

    sonnet = get_llm("balanced")

    @llm_breaker
    async def _write():
        return await retry_async(
            lambda: sonnet.ainvoke(writer_prompt, config=config),
            service="bedrock-synthesis-writer",
            max_attempts=2,
            backoff_base=5.0,
        )

    try:
        response = await _write()
    except Exception as e:
        logger.error("synthesis writer failed | thread={} | error={}", state["thread_id"], e)
        return {"answer": "I encountered an error preparing your answer. Please try again.", "follow_ups": []}

    raw = response.content if isinstance(response.content, str) else ""
    answer = parse_tag(raw, "answer") or _extract_answer_fallback(raw)
    follow_ups = _parse_follow_ups(raw)

    logger.info("synthesis DONE | thread={} | answer_len={} | follow_ups={}", state["thread_id"], len(answer), len(follow_ups))
    return {"answer": answer, "follow_ups": follow_ups}


def _build_current_date_context(current_date: str, all_columns: list[str], all_rows: list[list]) -> str:
    """Build temporal context block for synthesis.

    Tells the LLM what today's date is and whether the data snapshot is current.
    This ensures "X days until maturity" calculations use today, not position_date.
    """
    lines = [
        "TEMPORAL CONTEXT:",
        f"  Today's date: {current_date}",
        "  → Use today's date as the baseline for all 'days until' / 'days ago' calculations.",
        "  → Snapshot/position date columns in results are the data-as-of date, NOT today.",
    ]

    # Try to detect snapshot date columns and check staleness
    snapshot_col_keywords = ("as_of_date", "position_date", "snapshot_date", "report_date", "effective_date")
    try:
        today = datetime.date.fromisoformat(current_date)
        for i, col in enumerate(all_columns):
            col_lower = col.lower()
            if any(k in col_lower for k in snapshot_col_keywords) and all_rows:
                # Find a non-null value in this column
                val = None
                for row in all_rows[:10]:
                    if i < len(row) and row[i] is not None:
                        val = str(row[i])[:10]  # take YYYY-MM-DD part
                        break
                if val:
                    try:
                        snap_date = datetime.date.fromisoformat(val)
                        days_old = (today - snap_date).days
                        if days_old == 0:
                            lines.append(f"  Data snapshot ({col}): {val} — current (matches today).")
                        elif days_old > 0:
                            lines.append(
                                f"  ⚠ Data snapshot ({col}): {val} — {days_old} day(s) old. "
                                f"Results reflect data as of {val}, not today."
                            )
                        elif days_old < 0:
                            lines.append(f"  Data snapshot ({col}): {val} — future date, verify grain.")
                    except ValueError:
                        pass
                break
    except (ValueError, TypeError):
        pass

    return "\n".join(lines)


def _parse_follow_ups(raw: str) -> list[str]:
    from json_repair import loads as json_loads
    tag_content = parse_tag(raw, "follow_ups")
    if not tag_content:
        return []
    try:
        result = json_loads(tag_content)
        if isinstance(result, list):
            return [str(q) for q in result[:3]]
    except Exception:
        pass
    return []


def _extract_answer_fallback(raw: str) -> str:
    """Fallback when <answer> tags are absent.

    Strips <reasoning> and <follow_ups> blocks so leaked reasoning content
    never reaches the user as the answer body.
    """
    cleaned = re.sub(r"<reasoning>.*?</reasoning>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<follow_ups>.*?</follow_ups>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned).strip()
    return cleaned

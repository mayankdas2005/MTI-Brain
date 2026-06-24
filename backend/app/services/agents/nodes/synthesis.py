"""Node 4: synthesis — two-phase answer generation.

Phase 1 (Haiku):  INSIGHT_EXTRACTOR_PROMPT — reads raw data, extracts structured insights JSON.
Phase 2 (Sonnet): SYNTHESIS_PROMPT         — writes persona-formatted answer from insights only.

Sonnet never sees the raw data, eliminating a whole class of hallucination where the model
invents observations that aren't in the result set.
"""

from __future__ import annotations
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

import datetime
import json
import re

from app.core.logger import logger
from app.services.agents.helpers import _build_data_profile, build_mission_context, parse_tag
from app.services.agents.prompts import (
    REASONING_DIRECTIVE_NORMAL, REASONING_DIRECTIVE_DEEP,
    SYNTHESIS_PROMPT, INSIGHT_EXTRACTOR_PROMPT,
    _SYNTHESIS_PERSONA_STRUCTURES, _DEEP_ANALYSIS_PERSONA_RULES,
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
        "Note: Treat all figures as directional. Do NOT mention SQL, repair processes, or technical "
        "pipeline details in your answer — present the caveat in business terms only."
    ),
    "limit_without_order": (
        "Note: The result set was limited but rows were not explicitly ordered. "
        "The specific rows shown may vary on each execution."
    ),
}


_DEEP_ANALYSIS_EXTRACTION_INSTRUCTIONS = """
DEEP ANALYSIS MODE — additionally extract these two fields into the JSON object:

"concentration_challenge": Scan the data for the single sharpest challenge to the headline finding.
  Look for: one item/entity dominating >60% of an aggregate total; an average that hides extreme outliers;
  a positive headline trend that reverses when the top contributor is excluded.
  Write one concrete sentence with specific numbers if found (e.g. "2 counterparties account for 72%
  of $42M — excluding them, the remainder is flat MoM"). Set to null if no meaningful challenge exists.
  DEDUP RULE: The concentration_challenge MUST surface a DIFFERENT angle than findings[0].
  If the concentration insight is already the headline finding, set this field to null — do not repeat it.
  The purpose is "devil's advocate" — a counterpoint that CHALLENGES the main narrative, not confirms it.

"sql_explanation": In 2-3 sentences of plain business language (no SQL, no column names), describe:
  (1) what was counted or summed and from which business concept, (2) what time window or key filter
  was applied, (3) what was excluded if the FILTER CONTEXT below shows any exclusions.
  Example: "Summed total transaction value from wire transfers for settled USD transactions in June 2026
  above $1M. Excluded 47 pending transactions and 23 non-USD transactions."

FILTER CONTEXT (use for sql_explanation — do not fabricate exclusions not listed here):
{filter_directive}
"""


def _build_assumption_audit(state: AnalyticsState) -> list[str]:
    """Extract implicit assumptions from state — pure Python, no LLM."""
    if not state.get("deep_analysis"):
        return []

    lines: list[str] = []
    ir_list = state.get("semantic_ir_list") or []
    ir = ir_list[0] if ir_list else {}
    current_date_str = state.get("current_date") or datetime.date.today().isoformat()

    # Time range + partial-period completeness
    time_filter = ir.get("time_filter") or {}
    tf_value = time_filter.get("value")
    if isinstance(tf_value, list) and len(tf_value) == 2:
        start_str, end_str = str(tf_value[0])[:10], str(tf_value[1])[:10]
        try:
            start_d = datetime.date.fromisoformat(start_str)
            end_d = datetime.date.fromisoformat(end_str)
            today = datetime.date.fromisoformat(current_date_str)
            period_days = (end_d - start_d).days + 1
            elapsed_days = (today - start_d).days + 1
            if 0 < elapsed_days < period_days:
                pct = round(elapsed_days / period_days * 100)
                lines.append(f"Date range: {start_str} to {end_str} — {pct}% of period elapsed as of {current_date_str}")
            else:
                lines.append(f"Date range: {start_str} to {end_str}")
        except (ValueError, TypeError):
            lines.append(f"Date range: {start_str} to {end_str}")

    # Non-time filters — surface raw user phrasing
    filters = ir.get("filters") or []
    for f in filters:
        if f is time_filter:
            continue
        raw_val = f.get("raw_user_value") or f.get("value") or ""
        col = f.get("column_name") or ""
        op = f.get("operator") or "="
        if raw_val and col:
            col_label = col.replace("_", " ").title()
            lines.append(f"Filter applied: {col_label} {op} '{raw_val}'")

    # Low-confidence filters
    for lcf in (state.get("low_confidence_filters") or []):
        raw = lcf.get("raw_value") or ""
        resolved = lcf.get("resolved_value") or ""
        if raw and resolved and raw != resolved:
            lines.append(f"Approximate match: '{raw}' resolved to '{resolved}' (fuzzy)")

    # Row cap / truncation
    if state.get("result_was_truncated"):
        max_rows = state.get("max_rows") or 5000
        lines.append(f"Results capped at {max_rows:,} rows — full dataset may be larger")

    # Schema gaps
    directive_ctx = state.get("intent_directive_context") or ""
    gap_lines = [ln for ln in directive_ctx.splitlines() if ln.strip().startswith("SCHEMA_GAP")]
    for gap in gap_lines[:2]:
        gap_text = gap.replace("SCHEMA_GAP_JOIN", "join path unavailable").replace("SCHEMA_GAP_TABLE", "table unavailable").replace("SCHEMA_GAP_CONCEPT", "concept unmapped")
        lines.append(f"Data limitation: {gap_text.strip()}")

    return lines


def _fmt_number(v: float | int | None) -> str:
    if v is None:
        return "N/A"
    if abs(v) >= 1_000_000_000_000:
        return f"${v/1_000_000_000_000:.2f}T"
    if abs(v) >= 1_000_000_000:
        return f"${v/1_000_000_000:.2f}B"
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def _build_deep_analysis_sections(
    concentration_challenge: str | None,
    sql_explanation: str | None,
    assumption_lines: list[str],
    sensitivity_table: list[dict] | None,
    denominator_context: dict | None,
    temporal_projection: dict | None,
    tribal_facts: list[dict] | None = None,
) -> str:
    """Build the deep analysis supplementary sections string injected into SYNTHESIS_PROMPT."""
    parts: list[str] = []

    # Denominator context — inline note after main answer
    if denominator_context and denominator_context.get("share") is not None:
        concept = denominator_context.get("concept", "total")
        denom_val = denominator_context.get("value")
        share_pct = round(denominator_context["share"] * 100, 1)
        denom_fmt = _fmt_number(denom_val)
        parts.append(
            f"\n\n*Context: this represents **{share_pct}% of {concept}** ({denom_fmt} total)*"
        )

    # Temporal projection — inline note
    if temporal_projection:
        pct = temporal_projection.get("completeness_pct", 0)
        proj = temporal_projection.get("projected_total")
        period_end = temporal_projection.get("period_end", "")
        prior_at_same = temporal_projection.get("prior_period_at_same_point")
        prior_final = temporal_projection.get("prior_period_final")
        period_start_prior = temporal_projection.get("prior_period_start", "")

        proj_line = f"**Projected {period_end[:7]} total: {_fmt_number(proj)}**" if proj else ""
        pace_note = ""
        if prior_at_same is not None and prior_final is not None and prior_at_same > 0:
            pace_ratio = (temporal_projection.get("current_total", 0) / prior_at_same) if prior_at_same else None
            if pace_ratio is not None:
                direction = "ahead of" if pace_ratio > 1.05 else ("behind" if pace_ratio < 0.95 else "on pace with")
                pace_note = (
                    f"At this same point in {period_start_prior[:7]}, the value was {_fmt_number(prior_at_same)} "
                    f"(full period: {_fmt_number(prior_final)}) — current pace is **{direction}** prior period."
                )

        projection_text = f"*Period is {pct}% complete as of today. {proj_line}. {pace_note}*".strip(". ")
        parts.append(f"\n\n{projection_text}")

    # Devil's advocate challenge
    if concentration_challenge:
        parts.append(
            "\n\n---\n\n"
            "> **However:** " + concentration_challenge.strip()
        )

    # Threshold sensitivity table
    if sensitivity_table:
        rows_md = "\n".join(
            f"| {r['threshold_label']} | {r['count']:,} |"
            for r in sensitivity_table
        )
        parts.append(
            "\n\n**Threshold Sensitivity**\n\n"
            "| Threshold | Count |\n"
            "|-----------|-------|\n"
            + rows_md
        )

    # # SQL plain English explanation
    # if sql_explanation:
    #     parts.append(
    #         "\n\n<details>\n<summary>How this was computed</summary>\n\n"
    #         + sql_explanation.strip()
    #         + "\n</details>"
    #     )

    # # Assumption audit
    # if assumption_lines:
    #     bullet_list = "\n".join(f"- {ln}" for ln in assumption_lines)
    #     parts.append(
    #         "\n\n<details>\n<summary>Assumptions & Scope</summary>\n\n"
    #         + bullet_list
    #         + "\n</details>"
    #     )

    # # Tribal knowledge sources — list documents used so the reader can trace citations
    # if tribal_facts:
    #     citation_lines = [
    #         f"- **{f.get('label', 'Document')}**"
    #         for f in tribal_facts[:8]
    #     ]
    #     parts.append(
    #         "\n\n<details>\n<summary>Knowledge Sources</summary>\n\n"
    #         + "\n".join(citation_lines)
    #         + "\n</details>"
    #     )

    return "".join(parts) if parts else ""


async def synthesis(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("synthesis START | thread={} | persona={} | no_data={}", state["thread_id"], state.get("persona"), state.get("no_data"))

    # If any pipeline node set an error, skip the LLM entirely.
    # A detailed analytical report on a failed run is misleading — return a brief message.
    if state.get("error"):
        logger.info("synthesis | pipeline error — returning brief error | thread={} | error={}", state["thread_id"], str(state.get("error"))[:120])
        return {
            "answer": (
                "The query could not be completed. "
                "This is a transient infrastructure issue, not a data or query problem.\n\n"
                "**To retry:** try rephrasing with a narrower scope — a single entity, "
                "a shorter time period, or one metric at a time — to reduce query complexity."
            ),
            "follow_ups": [],
        }

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
    # When data_quality_flag=True, synthesis opens with #### Data Quality Concern
    # (enforced by the prompt DATA_INTEGRITY_GATE section + quality_context).
    data_quality_flag = state.get("data_quality_flag", False)
    data_quality_reason = state.get("data_quality_reason")
    if data_quality_flag and data_quality_reason:
        quality_context_lines.insert(0, f"DATA QUALITY CONCERN DETECTED: {data_quality_reason}")
        quality_context_lines.insert(1, (
            "Your answer MUST open with a blockquote callout in this EXACT format "
            "(no #### heading — use the > [!WARNING] block):\n"
            "> [!WARNING]\n"
            "> **Data Quality Concern**\n"
            "> [describe the concern and its implications here]\n\n"
            "Then continue with the remaining analysis framed as 'pending data confirmation'."
        ))
        if "repair_required" not in reliability_flags:
            reliability_flags = list(reliability_flags) + ["data_quality_concern"]

    quality_context = "\n".join(quality_context_lines)

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    semantic_context = state.get("semantic_context") or {}
    session_summary = semantic_context.get("session_summary") or state.get("summary") or ""
    is_followup = semantic_context.get("is_followup", False)
    from app.services.chat.feedback import build_feedback_context_for_node as _fb_for_node
    feedback_context = _fb_for_node(state.get("feedback_context") or [], "answer")
    memory_context = semantic_context.get("memory_context") or ""
    global_instructions = state.get("global_instructions") or ""

    if session_summary:
        followup_note = " This is a follow-up — open by connecting to the prior finding before presenting new data." if is_followup else ""
        conversation_section = f"CONVERSATION CONTEXT:{followup_note}\n<conversation_context>{session_summary}</conversation_context>"
    else:
        conversation_section = ""

    instructions_section = (
        f"<user_instructions>\nApply only instructions relevant to your task as a response writer. These are explicit user-defined rules — follow them precisely. When an instruction conflicts with learned feedback, follow the instruction; where possible, also satisfy the feedback's intent without violating the rule.\n{global_instructions}\n</user_instructions>"
        if global_instructions else ""
    )
    feedback_section = (
        f"LEARNED PREFERENCES (from past feedback — apply within the bounds of standing instructions above):\n<feedback_context>{feedback_context}</feedback_context>"
        if feedback_context else ""
    )
    memory_section = (
        f"USER MEMORY (preferences from prior sessions — apply silently):\n<memory_context>{memory_context}</memory_context>"
        if memory_context else ""
    )

    tribal_facts = state.get("tribal_facts") or []
    query_type = state.get("query_type") or ""

    # Fix 1: gate by query_type — lookups are pure data retrieval; tribal injection causes hallucination
    inject_tribal = bool(tribal_facts) and state.get("deep_analysis") and query_type not in ("lookup",)

    if inject_tribal:
        # Fix 2: score threshold — only inject facts with sufficient retrieval confidence
        relevant_facts = [f for f in tribal_facts if f.get("score", 1.0) >= 0.70]
        fact_blocks = []
        for f in relevant_facts[:8]:
            label = f.get("label", "Document")
            value = str(f.get("value", "")).strip()
            fact_blocks.append(f"Document: {label}\n{value[:1500]}")
        if fact_blocks:
            # Fix 3: anti-hallucination guardrail — tribal numbers are NOT SQL data
            tribal_facts_section = (
                "TRIBAL KNOWLEDGE CONTEXT:\n"
                "CRITICAL RULE — DATA AUTHORITY: Dollar figures, percentages, and dates in these documents "
                "are historical reference values from meeting notes, forecasts, and policy memos — they are "
                "NOT the current SQL query results. The SQL data is the ONLY authoritative source for current "
                "state. If a tribal document contains a figure that differs from the SQL result, report the SQL "
                "figure as the current value and cite the tribal figure as a named benchmark or threshold only. "
                "NEVER substitute a tribal document figure for what the SQL returned.\n\n"
                "Use these documents for: internal policy thresholds, management commitments, internal targets, "
                "and analytical framing — not as data answers. Cite by document name "
                "(e.g. 'per Group Treasury Policy', 'per CFO meeting notes of 2026-05-29').\n\n"
                + "\n\n---\n\n".join(fact_blocks)
            )
        else:
            tribal_facts_section = ""
    elif tribal_facts and not state.get("deep_analysis"):
        facts_text = "\n".join(
            f"  [{f.get('type', '')}] {f.get('label', '')} — {f.get('value', '')} (status: {f.get('status', 'active')})"
            for f in tribal_facts
        )
        tribal_facts_section = f"POLICY & LIMIT CONTEXT (cite relevant limits in your answer):\n{facts_text}"
    else:
        tribal_facts_section = ""

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

    # Deep analysis: build extraction instructions for Phase 1
    is_deep = bool(state.get("deep_analysis"))
    if is_deep and not no_data:
        filter_directive_text = state.get("filter_directive") or ""
        deep_extraction = _DEEP_ANALYSIS_EXTRACTION_INSTRUCTIONS.format(
            filter_directive=filter_directive_text or "(no filter context available)"
        )
    else:
        deep_extraction = ""

    # Build tables_section for follow_up_paths scope constraint
    _ir_list = state.get("semantic_ir_list") or []
    if _ir_list:
        _first = _ir_list[0]
        _anchor = _first.get("anchor_tables", []) if isinstance(_first, dict) else list(getattr(_first, "anchor_tables", []))
        tables_section = ", ".join(_anchor) if _anchor else "see data profile above"
    else:
        tables_section = "see data profile above"

    # ── Phase 1: Insight Extraction (Haiku — fast, data-facing) ──────────────
    # Haiku reads the raw data and produces a structured insights JSON.
    # This is the only phase that sees the raw data profile.

    extractor_prompt = INSIGHT_EXTRACTOR_PROMPT.format_messages(
        question=state["question"],
        persona=state.get("persona", "analyst"),
        current_date_context=current_date_context,
        flag_instructions_text=flag_instructions or "",
        quality_context=quality_context,
        no_data="YES" if no_data else "NO",
        zero_row_probe_result=zero_row_probe_result,
        data_profile=data_profile,
        tribal_facts_section=tribal_facts_section,
        conversation_context=conversation_section,
        deep_analysis_extraction=deep_extraction,
        tables_section=tables_section,
    )

    haiku = get_llm("fast")
    insights_json: str = "{}"
    concentration_challenge: str | None = None
    sql_explanation: str | None = None

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
            if is_deep:
                concentration_challenge = parsed.get("concentration_challenge") or None
                sql_explanation = parsed.get("sql_explanation") or None
            logger.info(
                "synthesis | insight extraction OK | thread={} | depth={} | findings={} | deep_challenge={}",
                state["thread_id"],
                parsed.get("depth", "?"),
                len(parsed.get("findings") or []),
                bool(concentration_challenge),
            )
        else:
            logger.warning("synthesis | insight extraction returned non-dict | thread={}", state["thread_id"])
    except Exception as e:
        logger.warning("synthesis | insight extraction failed, using empty insights | thread={} | error={}", state["thread_id"], e)

    # Build deep analysis supplementary sections (injected into Phase 2 prompt)
    assumption_lines = _build_assumption_audit(state) if is_deep else []
    deep_analysis_sections = _build_deep_analysis_sections(
        concentration_challenge=concentration_challenge,
        sql_explanation=sql_explanation,
        assumption_lines=assumption_lines,
        sensitivity_table=state.get("sensitivity_table") if is_deep else None,
        denominator_context=state.get("denominator_context") if is_deep else None,
        temporal_projection=state.get("temporal_projection") if is_deep else None,
        tribal_facts=tribal_facts if is_deep else None,
    ) if is_deep else ""

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

    if low_confidence_filters:
        lcf_lines = ["INTERNAL — filter confidence context (do NOT surface to user):"]
        for _f in low_confidence_filters:
            lcf_lines.append(
                f"  '{_f.get('column', '')}': resolved '{_f.get('raw_value', '')}' "
                f"resolved to '{_f.get('resolved_value', '')}' (fuzzy match — may not match intent)"
            )
        low_confidence_section = "\n".join(lcf_lines)
    else:
        low_confidence_section = ""

    # Build query_intent framing section for synthesis — tells synthesis what the user WANTED
    # to accomplish (from intake), separate from what the SQL actually computed (insights_json).
    # Rule (EC7): if a CONDITION/GOAL has no matching insight, synthesis must not fabricate.
    _qi_lines = state.get("query_intent") or []
    if _qi_lines:
        query_intent_section = (
            "USER'S STATED GOAL (from intake — describes what was REQUESTED, not what was computed):\n"
            + "\n".join(_qi_lines)
            + "\n\nRULE: If a CONDITION or GOAL above has no corresponding finding in PRE-EXTRACTED INSIGHTS, "
            "state that the computation was not available — do NOT infer outcomes from this section alone."
        )
    else:
        query_intent_section = ""

    _persona_key = (state.get("persona") or "analyst").lower()
    persona_structure = _SYNTHESIS_PERSONA_STRUCTURES.get(
        _persona_key, _SYNTHESIS_PERSONA_STRUCTURES["analyst"]
    )
    # Deep analysis: append integration rules so the LLM knows about
    # supplementary sections (However block, collapsibles, tribal sources).
    # Normal mode never sees these — saves tokens and avoids confusion.
    if is_deep:
        persona_structure += _DEEP_ANALYSIS_PERSONA_RULES.get(_persona_key, "")

    writer_prompt = SYNTHESIS_PROMPT.format_messages(
        persona=state.get("persona", "analyst"),
        question=state["question"],
        no_data_context=no_data_context,
        insights_json=insights_json,
        reasoning_directive=reasoning_directive,
        instructions_section=instructions_section,
        conversation_section=conversation_section,
        memory_section=memory_section,
        feedback_section=feedback_section,
        tribal_facts_section=tribal_facts_section,
        low_confidence_section=low_confidence_section,
        query_intent_section=query_intent_section,
        persona_structure=persona_structure,
        deep_analysis_sections=deep_analysis_sections,
    )
    _mission = build_mission_context(
        state,
        role="Narrate the result as a direct, complete answer to the user's question — no fabrication, no omission",
        feeds="chart_agent (framing context), user (final visible answer)",
    )
    writer_prompt[0].content = _mission + "\n\n" + writer_prompt[0].content

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
    result: dict = {"answer": answer, "follow_ups": follow_ups}
    if not state.get("is_retry"):
        result["messages"] = [HumanMessage(content=state["question"]), AIMessage(content=answer)]
    return result


def _build_current_date_context(current_date: str, all_columns: list[str], all_rows: list[list]) -> str:
    """Build temporal context block for synthesis.

    Tells the LLM what today's date is and whether the data snapshot is current.
    This ensures "X days until maturity" calculations use today, not position_date.
    """
    lines = [
        "TEMPORAL CONTEXT:",
        f"  Today's date: {current_date}",
        "  Use today's date as the baseline for all 'days until' / 'days ago' calculations.",
        "  Snapshot/position date columns in results are the data-as-of date, NOT today.",
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
                                f"  NOTE - Data snapshot ({col}): {val} — {days_old} day(s) old. "
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

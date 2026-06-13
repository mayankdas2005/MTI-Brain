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
    _SYNTHESIS_PERSONA_STRUCTURES,
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

_FULL_CONSULTING_GATES = (
    "CONSULTING STANDARD — MANDATORY. These answers are read by C-suite executives and must meet\n"
    "the standard of Bain, McKinsey, and BCG deliverables. Quality is enforced by three gates.\n"
    "Before writing each section, check these gates in <reasoning> and mark each ✓ or ✗. Rewrite\n"
    "any section that fails before finalizing.\n\n"
    "GATE 1 — PYRAMID PRINCIPLE: Every section must open with the business implication, not the data.\n"
    "  ✗ DESCRIPTIVE (FAIL): \"GR_FR tax payments total $924,760 due 2026-06-29.\"\n"
    "  ✓ IMPLICATION-FIRST (PASS): \"GR_FR faces its highest near-term liquidity pressure: a $924,760\n"
    "    tax obligation due 2026-06-29 cannot be deferred without penalty.\"\n"
    "  Test before writing: can the reader understand WHY this matters before they see the number?\n"
    "  If not, rewrite the opening sentence to lead with the consequence.\n\n"
    "GATE 2 — RECOMMENDATION SPECIFICITY: Every recommendation must contain all four elements or must\n"
    "not be written at all: (a) imperative action verb, (b) named functional owner, (c) hard deadline,\n"
    "  (d) quantified expected outcome. Followed by: \"If deferred: [specific cost, regulatory deadline,\n"
    "or risk event that worsens].\"\n"
    "  ✗ VAGUE (FAIL): \"Review the liquidity position across entities.\"\n"
    "  ✓ SPECIFIC (PASS): \"Confirm whether the $200M threshold applies at consolidated group level or\n"
    "    per entity — Group Treasury Finance, by end of this week. If deferred: every subsequent\n"
    "    funding decision is made against an unvalidated baseline.\"\n\n"
    "GATE 3 — SCENARIO GROUNDING: Every branch of Scenario Analysis must cite a specific number from\n"
    "PRE-EXTRACTED INSIGHTS. Omit branches without a grounded number — speculation is not analysis."
)

_BRIEF_CONSULTING_GATES = (
    "CONSULTING STANDARD: Answer first. Evidence second. Implication always.\n"
    "One key finding, one clear action, one consequence if deferred."
)

_DEPTH_CALIBRATION_FULL = (
    "DEPTH CALIBRATION:\n"
    "  SINGLE VALUE (1 row, 1 number): answer sentence + 1-2 implications + 1 action. No sections.\n"
    "  SIMPLE LOOKUP (2-10 rows): 2-3 key facts + 1 action if warranted. Skip empty sections.\n"
    "  RICH DATASET (10+ rows): use full persona structure. All sections apply.\n"
    "  NO DATA RETURNED: explain why in plain business terms.\n"
    "  RULE: ≥2 grounded findings required per section — except Verdict and Decision (always appear)."
)


def _build_consulting_gates(depth: str, decision_type: str) -> str:
    if depth == "rich_dataset" or decision_type in ("judgment", "multi_domain"):
        return _FULL_CONSULTING_GATES
    return _BRIEF_CONSULTING_GATES


def _build_depth_calibration(depth: str) -> str:
    _map = {
        "single_value": "DEPTH: single_value — write 1 answer sentence + 1-2 implications + 1 action. Do NOT force persona sections.",
        "simple_lookup": "DEPTH: simple_lookup — write 2-3 key facts + 1 action if warranted. Skip sections with fewer than 2 grounded points.",
        "rich_dataset": _DEPTH_CALIBRATION_FULL,
        "no_data": "DEPTH: no_data — explain why in plain business terms. Suggest what to change. No fake structure.",
    }
    return _map.get(depth, _DEPTH_CALIBRATION_FULL)


_DECISION_DIRECTIVES: dict[str, str | None] = {
    "breach_detection": (
        "DECISION ANSWER REQUIRED — lead with: YES [which periods breach + magnitude] or NO [minimum balance and when].\n"
        "Then: supporting data. Then: policy citation (from POLICY & LIMIT CONTEXT if available).\n"
        "Do NOT lead with a data table — the breach answer IS the answer."
    ),
    "judgment": (
        "JUDGMENT REQUIRED — three-step structure:\n"
        "  Step 1 — Data answer (SQL result only, no enterprise context)\n"
        "  Step 2 — Enterprise context (from POLICY & LIMIT CONTEXT: policy thresholds, commitments, obligations)\n"
        "  Step 3 — Does enterprise context CHANGE the data answer? State explicitly if YES.\n"
        "  NEVER say 'no action required' without first checking Step 2."
    ),
    "comparison": (
        "COMPARISON ANSWER REQUIRED — lead with: which is higher/lower, by how much, and whether the gap is material.\n"
        "Then: breakdown by entity/period. Do NOT describe both sides neutrally — state the delta."
    ),
    "trend_analysis": (
        "TREND ANSWER REQUIRED — lead with: direction (up/down/flat) + magnitude + rate of change.\n"
        "Then: supporting data. State the amount, not 'a decrease was observed'."
    ),
    "multi_domain": (
        "DOMAIN SUMMARY REQUIRED — one finding per DOMAIN line in intake order.\n"
        "Lead with the most material domain finding. Then others. Then: cross-domain risk if any."
    ),
    "lookup": None,
}

_MULTI_DOMAIN_DIRECTIVE = (
    "MULTI-DOMAIN STRUCTURE: organize findings one per domain in intake DOMAIN line order.\n"
    "Each domain finding: metric value + whether above/below target + one-sentence implication.\n"
    "Do NOT merge domains into a single narrative — keep them separate."
)

_RECONCILIATION_DIRECTIVE = (
    "RECONCILIATION ANSWER REQUIRED — lead with: total matched count + total discrepancy count + total discrepancy amount.\n"
    "Then: table showing matched vs unmatched records with source identifier and delta.\n"
    "State the reconciliation conclusion: is the discrepancy material? what is the likely cause?\n"
    "Do NOT describe matched records in detail — focus on the DIFFERENCES."
)


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

    tribal_facts = state.get("tribal_facts") or []
    if tribal_facts:
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
        tribal_facts_section=tribal_facts_section,
        conversation_context=conversation_section,
    )

    haiku = get_llm("fast")
    insights_json: str = "{}"
    _depth: str = "simple_lookup"
    parsed: dict = {}

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
            _depth = parsed.get("depth", "simple_lookup")
            logger.info(
                "synthesis | insight extraction OK | thread={} | depth={} | findings={}",
                state["thread_id"],
                _depth,
                len(parsed.get("findings") or []),
            )
        else:
            _depth = "simple_lookup"
            logger.warning("synthesis | insight extraction returned non-dict | thread={}", state["thread_id"])
    except Exception as e:
        _depth = "simple_lookup"
        logger.warning("synthesis | insight extraction failed, using empty insights | thread={} | error={}", state["thread_id"], e)

    # X3: Decision frame derivation — lead the synthesis with a clear YES/NO or direction
    # derived from the insights findings, scoped by decision_type.
    _decision_frame = ""
    if isinstance(parsed, dict) and _decision_type != "lookup":
        _insights_findings = parsed.get("findings") or []
        _key_finding = str(parsed.get("key_finding") or "")

        if _decision_type == "breach_detection":
            _breach_hits = [
                f for f in _insights_findings
                if any(w in (f.get("observation") or "").lower()
                       for w in ("breach", "below", "threshold", "flag", "minimum"))
            ]
            if _breach_hits:
                _decision_frame = f"BREACH DETECTED: {_breach_hits[0].get('observation', '')}"
            elif any(w in _key_finding.lower() for w in ("breach", "below", "threshold")):
                _decision_frame = f"BREACH DETECTED: {_key_finding}"
            elif _key_finding:
                _decision_frame = "NO BREACH: All periods above threshold"

        elif _decision_type == "judgment":
            _data_obs = (_insights_findings[0].get("observation") or _key_finding) if _insights_findings else _key_finding
            _policy_note = " Enterprise context available." if state.get("tribal_facts") else " No enterprise context retrieved."
            if _data_obs:
                _decision_frame = f"Data answer: {_data_obs}.{_policy_note}"

        elif _decision_type == "comparison":
            _delta_hits = [
                f for f in _insights_findings
                if any(w in (f.get("observation") or "").lower()
                       for w in ("higher", "lower", "above", "below", "delta", "difference", "more", "less", "than"))
            ]
            if _delta_hits:
                _decision_frame = f"COMPARISON: {_delta_hits[0].get('observation', '')}"

        elif _decision_type == "trend_analysis":
            _trend_hits = [
                f for f in _insights_findings
                if any(w in (f.get("observation") or "").lower()
                       for w in ("increased", "decreased", "grew", "declined", "up", "down", "rising", "falling"))
            ]
            if _trend_hits:
                _decision_frame = f"TREND: {_trend_hits[0].get('observation', '')}"

    decision_frame_section = (
        f"DECISION FRAME (derived from data — lead your answer with this):\n{_decision_frame}"
    ) if _decision_frame else ""

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
                f"→ '{_f.get('resolved_value', '')}' (fuzzy match — may not match intent)"
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

    _decision_type = (state.get("decision_type") or "lookup").lower()
    _decision_directive = _DECISION_DIRECTIVES.get(_decision_type)
    _has_multi_domain = state.get("has_multi_domain") or False
    _has_reconciliation = state.get("has_reconciliation") or False

    directives = []
    if _decision_directive:
        directives.append(_decision_directive)
    if _has_multi_domain and _decision_type != "multi_domain":
        directives.append(_MULTI_DOMAIN_DIRECTIVE)
    if _has_reconciliation:
        directives.append(_RECONCILIATION_DIRECTIVE)

    if directives:
        combined = "\n\n".join(directives)
        if query_intent_section:
            query_intent_section = combined + "\n\n" + query_intent_section
        else:
            query_intent_section = combined
        logger.info(
            "synthesis | decision_directive_injected | type={} | has_multi_domain={} | has_reconciliation={} | thread={}",
            _decision_type, _has_multi_domain, _has_reconciliation, state["thread_id"],
        )

    _persona_key = (state.get("persona") or "executive").lower()
    persona_structure = _SYNTHESIS_PERSONA_STRUCTURES.get(
        _persona_key, _SYNTHESIS_PERSONA_STRUCTURES["executive"]
    )

    # Y3: build sql_computation_section for synthesis
    _impl = state.get("sql_computation_summary") or []
    sql_computation_section = (
        "COMPUTED COLUMNS AVAILABLE IN RESULT (reference these by name in your answer):\n"
        + "\n".join(f"  - {c}" for c in _impl)
    ) if _impl else ""

    writer_prompt = SYNTHESIS_PROMPT.format_messages(
        persona=state.get("persona", "executive"),
        question=state["question"],
        no_data_context=no_data_context,
        insights_json=insights_json,
        reasoning_directive=reasoning_directive,
        conversation_section=conversation_section,
        memory_section=memory_section,
        feedback_section=feedback_section,
        tribal_facts_section=tribal_facts_section,
        low_confidence_section=low_confidence_section,
        query_intent_section=query_intent_section,
        persona_structure=persona_structure,
        consulting_gates_section=_build_consulting_gates(_depth, _decision_type),
        depth_calibration_section=_build_depth_calibration(_depth),
        decision_frame_section=decision_frame_section,
        sql_computation_section=sql_computation_section,
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

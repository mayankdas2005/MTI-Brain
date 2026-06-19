"""compute_confidence — post-processing confidence scorer.

Called in stream_pipeline (pipeline.py) after the astream_events loop completes,
using the locally-captured state dict. Not a LangGraph node.

Architecture: two responsibilities are now separated.
  1. Score  — computed deterministically in Python from state signals (no LLM variance).
  2. Explanation — single Haiku call that receives the pre-computed score and clean
                   business context and writes one user-facing sentence.

Returns a dict: {score: int 0-100, label: str, explanation: str}
Returns None for general_chat (no data grounding applicable).
"""

import re

import json_repair

from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import _build_data_profile
from app.services.agents.prompts import CONFIDENCE_JUDGE_PROMPT


def _label(score: int) -> str:
    if score >= 75:
        return "High"
    if score >= 55:
        return "Medium"
    if score >= 35:
        return "Low"
    return "Very Low"


def _compute_score(state: dict) -> int:
    """Deterministic confidence score from state signals — no LLM involved.

    Two-layer model:

    Layer 1 — Planning confidence (schema coverage before execution):
      If directive_writer emitted CONFIDENCE_NOTE -> use it × 80 as base.
      If no CONFIDENCE_NOTE -> base 60, then deduct schema gaps and unresolved joins.

    Layer 2 — Execution confidence (applied on top regardless):
      Low-conf filter resolutions: -10 each (cap -25)
      SQL repairs required:        -12 each (cap -25)
      No data returned:            -25
      Data quality flag (when enabled): -15

    Bonus (earned, not assumed):
      Clean run (0 repairs + has data + 0 low-conf): +15
      Has data + 0 repairs (partial clean): +5

    Floor / Ceiling: 5 / 85
    """
    ir = (state.get("semantic_ir_list") or [{}])[0]

    intent_context = (state.get("intent_directive_context") or "").strip()
    m = re.search(r"CONFIDENCE_NOTE:\s*([\d.]+)", intent_context, re.IGNORECASE)
    if m:
        try:
            score = int(round(float(m.group(1)) * 80))
        except (ValueError, TypeError):
            score = 60
    else:
        score = 60
        schema_gaps = ir.get("schema_gaps") or []
        score -= min(len(schema_gaps) * 8, 28)
        unresolved = ir.get("unresolved_join_pairs") or []
        score -= min(len(unresolved) * 15, 30)

    # Execution-time signals — always applied
    low_conf = state.get("low_confidence_filters") or []
    score -= min(len(low_conf) * 10, 25)
    repair_count = int(state.get("repair_count") or 0)
    score -= min(repair_count * 12, 25)
    if state.get("no_data"):
        score -= 25

    # Data quality signal — only when checker is enabled
    if settings.DATA_QUALITY_CHECKER_ENABLED and state.get("data_quality_flag"):
        score -= 15

    # Execution bonus — earned by clean runs
    has_data = not state.get("no_data")
    if repair_count == 0 and has_data and not low_conf:
        score += 15
    elif has_data and repair_count == 0:
        score += 5

    return max(5, min(85, score))


def _build_business_signals(state: dict) -> str:
    """Build a clean, business-language summary of what affected data quality.

    Passed to the explanation LLM so it can reference real issues without
    ever seeing internal terms like SCHEMA_GAP, SQL repair, or filter_directive.
    """
    lines = []
    ir = (state.get("semantic_ir_list") or [{}])[0]

    schema_gaps = ir.get("schema_gaps") or []
    if schema_gaps:
        lines.append(f"- {len(schema_gaps)} requested concept(s) could not be fully matched to available data fields")

    low_conf = state.get("low_confidence_filters") or []
    if low_conf:
        lines.append(f"- {len(low_conf)} filter value(s) were matched approximately rather than exactly")

    unresolved = ir.get("unresolved_join_pairs") or []
    if unresolved:
        lines.append(f"- {len(unresolved)} data relationship(s) between tables could not be fully confirmed")

    if int(state.get("repair_count") or 0) > 0:
        lines.append("- Result is directional — cross-validate key figures before escalating")

    if state.get("no_data"):
        lines.append("- No matching records were found for the requested criteria")

    if settings.DATA_QUALITY_CHECKER_ENABLED:
        dq_reason = state.get("data_quality_reason")
        if dq_reason:
            lines.append(f"- Data quality issue detected: {dq_reason}")

    reliability_flags = state.get("reliability_flags") or []
    _flag_map = {
        "time_filter_relaxed": "Time filter was broadened — results may span a wider period than requested",
        "filters_relaxed": "Filters were removed to find any data — results may be broader than intended",
        "high_null_ratio": "A significant portion of returned values are empty",
        "limit_without_order": "Result rows are unordered — specific items shown may vary on re-run",
        "unexpected_row_count": "Row count is higher than expected for this metric type",
    }
    for flag in reliability_flags:
        msg = _flag_map.get(flag)
        if msg:
            lines.append(f"- {msg}")

    return "\n".join(lines) if lines else "- No data quality issues detected"


async def compute_confidence(state: dict) -> dict | None:
    if state.get("question_type") == "general_chat":
        return None

    # ── Step 1: deterministic score (no LLM) ─────────────────────────────────
    score = _compute_score(state)
    label = _label(score)

    # ── Step 2: business-language explanation (Haiku) ─────────────────────────
    all_rows: list = state.get("_rows") or []
    all_cols: list = state.get("_cols") or []
    data_profile = _build_data_profile(
        columns=all_cols,
        rows=all_rows,
        query_summary=state.get("query_summary"),
    )
    business_signals = _build_business_signals(state)

    prompt = CONFIDENCE_JUDGE_PROMPT.format(
        score=score,
        label=label,
        question=state.get("question", ""),
        data_profile=data_profile,
        business_signals=business_signals,
        answer=(state.get("answer", "") or "")[:400],
    )

    try:
        from app.core.retry import retry_async
        _llm = get_llm("fast")
        response = await retry_async(
            lambda: _llm.ainvoke([HumanMessage(content=prompt)]),
            service="bedrock-confidence",
            max_attempts=2,
            backoff_base=5.0,
        )
        raw = (response.content or "").strip()
        parsed = json_repair.loads(raw)
        if not isinstance(parsed, dict):
            return {"score": score, "label": label, "explanation": ""}
        explanation = parsed.get("explanation") or ""
        return {"score": score, "label": label, "explanation": explanation}
    except Exception:
        return {"score": score, "label": label, "explanation": ""}

"""compute_confidence — post-processing confidence scorer.

Called in stream_pipeline (pipeline.py) after the astream_events loop completes,
using the locally-captured state dict. Not a LangGraph node.

Makes a single Haiku LLM call with all relevant context (question, data, pipeline
signals, answer) and lets the model return the final score and explanation.

Returns a dict: {score: int 0-100, label: str, explanation: str}
Returns None for general_chat (no data grounding applicable).
"""

import json_repair

from langchain_core.messages import HumanMessage

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import _build_data_profile
from app.services.agents.prompts import CONFIDENCE_JUDGE_PROMPT


def _label(score: int) -> str:
    if score >= 80:
        return "High"
    if score >= 60:
        return "Medium"
    if score >= 40:
        return "Low"
    return "Very Low"


async def compute_confidence(state: dict) -> dict | None:
    if state.get("question_type") == "general_chat":
        return None

    # ── Directive signals (new — richer grounding than old semantic_context) ──
    intent_context   = (state.get("intent_directive_context") or "").strip()
    filter_directive = (state.get("filter_directive") or "").strip()
    schema_directive = (state.get("schema_directive") or "").strip()

    # Summarize filter directive: keep only quality-signal lines (low confidence, warnings, list complete)
    _filter_kw = ("[low confidence", "[fuzzy match", "[warning:", "low_confidence_filters", "filter_list_complete")
    filter_lines = [ln for ln in filter_directive.splitlines()
                    if any(kw in ln.lower() for kw in _filter_kw)]
    filter_directive_summary = (
        "\n".join(filter_lines) if filter_lines
        else "(all filters resolved at high confidence)"
    )

    # Summarize schema directive: anchor tables, join chain, unresolved pairs, measures/dimensions
    _schema_kw = ("ANCHOR_TABLES", "JOIN_CHAIN", "UNRESOLVED_PAIRS", "↔", "MEASURES", "DIMENSIONS")
    schema_lines = [ln for ln in schema_directive.splitlines()
                    if any(kw in ln.upper() for kw in _schema_kw)]
    schema_directive_summary = (
        "\n".join(schema_lines[:15]) if schema_lines
        else "(schema structure not available)"
    )

    # ── Result data — same shared builder used by synthesis and chart_agent ──
    all_rows: list = state.get("_rows") or []
    all_cols: list = state.get("_cols") or []
    data_profile = _build_data_profile(
        columns=all_cols,
        rows=all_rows,
        query_summary=state.get("query_summary"),
    )

    # ── Pipeline signals ────────────────────────────────────────────────────
    reliability_flags = state.get("reliability_flags") or []

    prompt = CONFIDENCE_JUDGE_PROMPT.format(
        intent_context=intent_context[:1200] or "(not available)",
        filter_directive_summary=filter_directive_summary,
        schema_directive_summary=schema_directive_summary,
        no_data=state.get("no_data", False),
        repair_count=state.get("repair_count", 0) or 0,
        recompile_count=state.get("recompile_count", 0) or 0,
        reliability_flags=", ".join(reliability_flags) if reliability_flags else "None",
        error=state.get("error") or state.get("execution_error") or "None",
        question=state.get("question", ""),
        data_profile=data_profile,
        answer=(state.get("answer", "") or "")[:500],
    )

    try:
        response = await get_llm("fast").ainvoke([HumanMessage(content=prompt)])
        raw = (response.content or "").strip()
        parsed = json_repair.loads(raw)
        if not isinstance(parsed, dict):
            return None
        raw_score = parsed.get("score")
        if raw_score is None:
            return None
        score = max(0, min(100, int(round(float(raw_score)))))
        explanation = parsed.get("explanation") or ""
        return {
            "score": score,
            "label": _label(score),
            "explanation": explanation,
        }
    except Exception:
        return None

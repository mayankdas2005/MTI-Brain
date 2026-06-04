"""compute_confidence — post-processing confidence scorer.

Called in stream_pipeline (pipeline.py) after the astream_events loop completes,
using the locally-captured state dict. Not a LangGraph node.

Makes a single Haiku LLM call with all relevant context (question, data, pipeline
signals, answer) and lets the model return the final score and explanation.

Returns a dict: {score: int 0-100, label: str, explanation: str}
Returns None for general_chat (no data grounding applicable).
"""

import json

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

    # ── Result data (pre-computed in pipeline.py, same values used in done event) ──
    all_rows: list = state.get("_rows") or []
    all_cols: list = state.get("_cols") or []


    data_profile = _build_data_profile(
        columns=all_cols,
        rows=all_rows,
        query_summary=state.get("query_summary"),
    )

    # ── Semantic context ─────────────────────────────────────────────────────
    sc = state.get("semantic_context") or {}
    ri = state.get("resolved_intent") or {}

    semantic_context_str = (
        f"Intents: {', '.join(str(i) for i in sc.get('intents') or []) or 'None'}\n"
        f"Business terms: {', '.join(str(t) for t in sc.get('business_terms') or []) or 'None'}\n"
        f"Query patterns: {', '.join(str(p) for p in sc.get('query_patterns') or []) or 'None'}"
    )

    resolved_intent_str = (
        f"Intent: {ri.get('intent') or 'None'}\n"
        f"Anchor tables: {', '.join(str(t) for t in ri.get('anchor_tables') or []) or 'None'}\n"
        f"Template ID: {ri.get('template_id') or 'None'}"
    )

    # ── Pipeline signals ─────────────────────────────────────────────────────
    reliability_flags = state.get("reliability_flags") or []
    total_corrections = (state.get("repair_count", 0) or 0) + (state.get("recompile_count", 0) or 0)
    error = state.get("error") or state.get("execution_error") or "None"

    prompt = CONFIDENCE_JUDGE_PROMPT.format(
        question=state.get("question", ""),
        semantic_context=semantic_context_str,
        resolved_intent=resolved_intent_str,
        no_data=state.get("no_data", False),
        total_corrections=total_corrections,
        reliability_flags=", ".join(reliability_flags) if reliability_flags else "None",
        error=error,
        data_profile=data_profile,
        answer=state.get("answer", "") or "",
    )
    
    try:
        response = await get_llm("fast").ainvoke([HumanMessage(content=prompt)])
        raw = (response.content if hasattr(response, "content") else str(response)).strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
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

"""answer_synthesis_node — parallel narrative + chart generation."""

from __future__ import annotations

import asyncio
import json
import time

from langchain_core.messages import AIMessage

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import (
    _build_data_summary,
    _NARRATIVE_SAMPLE_CAP,
    parse_tag,
    parse_json_from_response,
)
from app.services.agents.prompts import ANSWER_SYNTHESIS_PROMPT, CHART_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State


def _format_tribal_facts(facts: list[dict]) -> str:
    if not facts:
        return "None."
    return "\n".join(
        f"[{f.get('type', '?')}] {f.get('label', '?')}: {f.get('value', '')}"
        for f in facts[:10]
    )


def _format_results_sample(columns: list[str], rows: list[list], limit: int = 30) -> str:
    if not columns or not rows:
        return "No data returned."
    header = " | ".join(columns)
    sample = rows[:limit]
    lines = [header, "-" * len(header)]
    for row in sample:
        lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
    if len(rows) > limit:
        lines.append(f"... ({len(rows) - limit} more rows)")
    return "\n".join(lines)


async def answer_synthesis_node(state: State) -> dict:
    question = state.get("question", "")
    intent = state.get("intent", "")
    persona = state.get("persona", "Analyst")
    kg_columns = state.get("kg_columns", [])
    kg_rows = state.get("kg_rows", [])
    tribal_facts = state.get("tribal_facts", [])
    evidence = state.get("evidence", [])
    reasoning = state.get("reasoning", "")
    t0 = time.perf_counter()

    if not kg_columns or not kg_rows:
        answer = (
            "No data was returned for this query. "
            "This could mean no records match the filter criteria, "
            "or the data does not exist in the Knowledge Graph."
        )
        messages = list(state.get("messages", [])) + [AIMessage(content=answer)]
        step = {"node": "answer_synthesis", "label": "No data response",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"answer": answer, "follow_ups": [], "chart_json": None,
                "messages": messages, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    col_stats, null_notes, spread_sample = _build_data_summary(kg_columns, kg_rows)

    sample_lines = []
    for row in spread_sample[:_NARRATIVE_SAMPLE_CAP]:
        sample_lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))

    tier = "deep" if state.get("deep_analysis") else "balanced"
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    narrative_coro = (ANSWER_SYNTHESIS_PROMPT | get_llm(tier)).ainvoke({
        "question": question,
        "intent": intent,
        "persona": persona,
        "col_stats": col_stats,
        "results_sample": _format_results_sample(kg_columns, kg_rows),
        "row_count": len(kg_rows),
        "tribal_facts": _format_tribal_facts(tribal_facts),
        "evidence": ", ".join(evidence) if evidence else "None.",
        "reasoning": reasoning or "None.",
        "reasoning_directive": reasoning_directive,
    })

    chart_coro = (CHART_PROMPT | get_llm("fast")).ainvoke(
        {
            "question": question,
            "columns": ", ".join(kg_columns),
            "sample_rows": "\n".join(sample_lines) if sample_lines else "No data",
            "row_count": len(kg_rows),
            "col_stats": col_stats,
            "reasoning_directive": REASONING_DIRECTIVE_NORMAL,
        },
    )

    narrative_raw, chart_raw = await asyncio.gather(narrative_coro, chart_coro)

    narrative_text = narrative_raw.content if hasattr(narrative_raw, "content") else str(narrative_raw)
    chart_text = chart_raw.content if hasattr(chart_raw, "content") else str(chart_raw)

    answer = parse_tag(narrative_text, "answer") or narrative_text
    follow_ups_raw = parse_tag(narrative_text, "follow_ups")
    try:
        follow_ups = json.loads(follow_ups_raw) if follow_ups_raw else []
    except (json.JSONDecodeError, ValueError):
        follow_ups = []

    # Parse chart JSON from <chart> tag; fall back to raw JSON search
    chart_tag = parse_tag(chart_text, "chart")
    chart_json = parse_json_from_response(chart_tag if chart_tag else chart_text)

    messages = list(state.get("messages", [])) + [AIMessage(content=answer)]
    step = {
        "node": "answer_synthesis",
        "label": "Preparing your answer",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "answer": answer,
        "follow_ups": follow_ups,
        "chart_json": chart_json or None,
        "messages": messages,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

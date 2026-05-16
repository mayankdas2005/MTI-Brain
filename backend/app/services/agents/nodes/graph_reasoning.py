"""graph_reasoning_node — derive patterns and evidence from SPARQL results."""

from __future__ import annotations

import json
import time

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import GRAPH_REASONING_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State


def _format_results_sample(columns: list[str], rows: list[list], limit: int = 20) -> str:
    if not columns or not rows:
        return "No results."
    header = " | ".join(columns)
    sample = rows[:limit]
    lines = [header, "-" * len(header)]
    for row in sample:
        lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
    if len(rows) > limit:
        lines.append(f"... ({len(rows) - limit} more rows)")
    return "\n".join(lines)


def _format_tribal_facts(facts: list[dict]) -> str:
    if not facts:
        return "None."
    return "\n".join(
        f"[{f.get('type', '?')}] {f.get('label', '?')}: {f.get('value', '')}"
        for f in facts[:10]
    )


async def graph_reasoning_node(state: State) -> dict:
    question = state.get("question", "")
    intent = state.get("intent", "")
    persona = state.get("persona", "Analyst-F")
    kg_columns = state.get("kg_columns", [])
    kg_rows = state.get("kg_rows", [])
    tribal_facts = state.get("tribal_facts", [])
    t0 = time.perf_counter()

    results_sample = _format_results_sample(kg_columns, kg_rows)
    tribal_str = _format_tribal_facts(tribal_facts)

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    chain = GRAPH_REASONING_PROMPT | get_llm("balanced")
    raw = await chain.ainvoke({
        "question": question,
        "intent": intent,
        "persona": persona,
        "results_sample": results_sample,
        "row_count": len(kg_rows),
        "tribal_facts": tribal_str,
        "reasoning_directive": reasoning_directive,
    })
    text = raw.content if hasattr(raw, "content") else str(raw)

    reasoning = parse_tag(text, "reasoning") or ""

    evidence_raw = parse_tag(text, "evidence")
    try:
        evidence = json.loads(evidence_raw) if evidence_raw else []
    except (json.JSONDecodeError, ValueError):
        evidence = [e.strip() for e in evidence_raw.split(",") if e.strip()]

    step = {
        "node": "graph_reasoning",
        "label": "Analyzing results",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "reasoning": reasoning,
        "evidence": evidence,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

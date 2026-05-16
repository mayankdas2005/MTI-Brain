"""final_reflector_node — overall answer correctness judge for outer loop."""

from __future__ import annotations

import time

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import FINAL_REFLECTOR_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State


def _summarize_scratchpad(scratchpad: dict) -> str:
    if not scratchpad:
        return "No sub-question results."
    lines = []
    for sqid, result in scratchpad.items():
        status = result.get("status", "unknown")
        reflection = result.get("reflection", "")
        answer_snippet = (result.get("answer", "") or "")[:200]
        lines.append(f"[{sqid}] status={status} | {reflection} | answer: {answer_snippet}…")
    return "\n".join(lines)


async def final_reflector_node(state: State) -> dict:
    question = state.get("question", "")
    persona = state.get("persona", "Executive")
    scratchpad = state.get("scratchpad", {})
    t0 = time.perf_counter()

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    chain = FINAL_REFLECTOR_PROMPT | get_llm("balanced")
    raw = await chain.ainvoke({
        "question": question,
        "persona": persona,
        "scratchpad_summary": _summarize_scratchpad(scratchpad),
        "reasoning_directive": reasoning_directive,
    })
    text = raw.content if hasattr(raw, "content") else str(raw)
    reflection = parse_tag(text, "reflection") or text.strip()

    combined_answer_parts: list[str] = []
    combined_columns: list[str] = []
    combined_rows: list[list] = []
    combined_evidence: list[str] = []

    for sqid, result in scratchpad.items():
        if result.get("status") in ("completed",):
            if result.get("answer"):
                combined_answer_parts.append(f"**{sqid}**: {result['answer']}")
            if not combined_columns and result.get("kg_columns"):
                combined_columns = result["kg_columns"]
            combined_rows.extend(result.get("kg_rows", []))
            combined_evidence.extend(result.get("evidence", []))

    step = {
        "node": "final_reflector",
        "label": "Final quality check",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "final_reflection": reflection,
        "kg_columns": combined_columns,
        "kg_rows": combined_rows,
        "kg_row_count": len(combined_rows),
        "evidence": combined_evidence,
        "reasoning": "\n\n".join(combined_answer_parts),
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

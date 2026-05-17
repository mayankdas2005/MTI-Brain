"""step_reflector_node — per-sub-Q LLM judge (PASS / FAIL / SKIP)."""

from __future__ import annotations

import asyncio
import time

from app.services.agents.bedrock import get_llm
from app.services.agents.prompts import STEP_REFLECTOR_PROMPT
from app.services.agents.state import State


async def _reflect_one(sq: dict, result: dict, llm) -> dict:
    results_sample = ""
    cols = result.get("kg_columns", [])
    rows = result.get("kg_rows", [])
    if cols and rows:
        header = " | ".join(cols)
        results_sample = header + "\n" + "\n".join(
            " | ".join(str(v) if v is not None else "NULL" for v in r) for r in rows[:10]
        )

    raw = await (STEP_REFLECTOR_PROMPT | llm).ainvoke(
        {
            "sub_question": sq.get("question", ""),
            "intent": sq.get("intent", ""),
            "sparql": result.get("sparql", ""),
            "row_count": result.get("kg_row_count", 0),
            "results_sample": results_sample or "No data",
            "error": result.get("error", ""),
        },
        config={"tags": ["no_stream"]},
    )
    verdict = (raw.content if hasattr(raw, "content") else str(raw)).strip()

    if verdict.startswith("PASS"):
        return {**result, "status": "completed", "reflection": "PASS"}
    elif verdict.startswith("SKIP"):
        return {**result, "status": "skipped", "reflection": verdict}
    else:
        return {**result, "status": "needs_repair", "reflection": verdict}


async def step_reflector_node(state: State) -> dict:
    sub_questions = state.get("sub_questions", [])
    scratchpad = dict(state.get("scratchpad", {}))
    t0 = time.perf_counter()

    llm = get_llm("fast")

    to_reflect = [sq for sq in sub_questions if sq["id"] in scratchpad and
                  scratchpad[sq["id"]].get("status") not in ("skipped", "reflected")]

    if not to_reflect:
        step = {"node": "step_reflector", "label": "No sub-Qs to reflect on",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"pipeline_steps": state.get("pipeline_steps", []) + [step]}

    reflect_tasks = [_reflect_one(sq, scratchpad[sq["id"]], llm) for sq in to_reflect]
    results = await asyncio.gather(*reflect_tasks)

    updated_scratchpad = {r["id"]: r for r in results}

    step = {
        "node": "step_reflector",
        "label": f"Reflected on {len(results)} sub-Qs",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "pass_count": sum(1 for r in results if r.get("status") == "completed"),
        "fail_count": sum(1 for r in results if r.get("status") == "needs_repair"),
    }
    return {
        "scratchpad": updated_scratchpad,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

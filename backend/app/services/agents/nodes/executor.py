"""executor_node — topological iteration + parallel fan-out for sub-questions.

Each sub-question is run through the compiled inner graph (domain_specialist
through visualization). Independent sub-questions are executed in parallel
up to PARALLEL_FANOUT.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from app.core.logger import logger
from app.services.agents.state import State

PARALLEL_FANOUT = 4


async def _run_sub_question(sq: dict, state: State, inner_graph) -> dict:
    """Invoke the inner graph for a single sub-question and return result dict."""
    from app.services.agents.helpers import _format_scratchpad_context

    scratchpad = state.get("scratchpad", {})
    dep_context = _format_scratchpad_context(scratchpad, sq.get("depends_on", []))

    sub_initial = {
        "question": sq["question"],
        "question_type": "kg_query",
        "persona": state.get("persona", "Analyst-F"),
        "complexity": "simple",
        "intent": sq.get("intent", ""),
        "routing": state.get("routing", "kg_only"),
        "ontology_terms": state.get("ontology_terms", []),
        "tribal_facts": state.get("tribal_facts", []),
        "sparql": "",
        "sparql_error": "",
        "sparql_retries": 0,
        "kg_results": [],
        "kg_columns": [],
        "kg_rows": [],
        "kg_row_count": 0,
        "evidence": [],
        "reasoning": "",
        "answer": "",
        "chart_json": None,
        "viz_spec": None,
        "follow_ups": [],
        "hil_required": False,
        "hil_approved": None,
        "governance_halt": None,
        "pipeline_steps": [],
        "messages": [],
        "summary": dep_context,
    }

    config = {"configurable": {"thread_id": f"subq-{sq['id']}-{int(time.time())}"}}
    try:
        result = await inner_graph.ainvoke(sub_initial, config=config)
        return {
            "id": sq["id"],
            "status": "completed" if not result.get("governance_halt") else "failed",
            "answer": result.get("answer", ""),
            "sparql": result.get("sparql", ""),
            "kg_columns": result.get("kg_columns", []),
            "kg_rows": result.get("kg_rows", []),
            "kg_row_count": result.get("kg_row_count", 0),
            "evidence": result.get("evidence", []),
            "error": result.get("governance_halt") or result.get("sparql_error") or "",
        }
    except Exception as e:
        logger.warning(f"Sub-question {sq['id']} execution failed: {e}")
        return {"id": sq["id"], "status": "failed", "error": str(e),
                "answer": "", "sparql": "", "kg_columns": [], "kg_rows": [],
                "kg_row_count": 0, "evidence": []}


async def executor_node(state: State) -> dict:
    from app.services.agents.graph import get_inner_graph

    sub_questions = list(state.get("sub_questions", []))
    scratchpad = dict(state.get("scratchpad", {}))
    budget_used = dict(state.get("budget_used", {"tokens": 0, "seconds": 0, "fuseki_rows": 0, "usd": 0.0}))
    t0 = time.perf_counter()
    inner_graph = get_inner_graph()

    completed_ids = {sqid for sqid, res in scratchpad.items() if res.get("status") in ("completed", "skipped")}

    def _is_ready(sq: dict) -> bool:
        return (
            sq["status"] == "pending"
            and all(dep in completed_ids for dep in sq.get("depends_on", []))
        )

    new_scratchpad: dict = {}
    updated_sub_questions = [dict(sq) for sq in sub_questions]

    remaining = [sq for sq in updated_sub_questions if sq["status"] == "pending"]
    rounds = 0
    while remaining and rounds < 20:
        rounds += 1
        ready = [sq for sq in remaining if _is_ready(sq)]
        if not ready:
            break

        batch = ready[:PARALLEL_FANOUT]
        results = await asyncio.gather(*[_run_sub_question(sq, state, inner_graph) for sq in batch])

        for res in results:
            sqid = res["id"]
            new_scratchpad[sqid] = res
            completed_ids.add(sqid)
            budget_used["fuseki_rows"] = budget_used.get("fuseki_rows", 0) + res.get("kg_row_count", 0)

        for sq in updated_sub_questions:
            if sq["id"] in {r["id"] for r in results}:
                r = next(r for r in results if r["id"] == sq["id"])
                sq["status"] = r["status"]
                sq["attempt"] = sq.get("attempt", 0) + 1

        remaining = [sq for sq in updated_sub_questions if sq["status"] == "pending"]

    budget_used["seconds"] = round(time.perf_counter() - t0, 2)
    step = {
        "node": "executor",
        "label": f"Executed {len(new_scratchpad)} sub-questions",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "sub_questions": updated_sub_questions,
        "scratchpad": new_scratchpad,
        "budget_used": budget_used,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

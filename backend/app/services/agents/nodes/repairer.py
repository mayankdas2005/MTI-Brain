"""repairer_node — L1/L2/L3 escalation repair for failed sub-questions.

L1: Regenerate SPARQL with error feedback (max 2 per sub-Q)
L2: Rewrite the sub-Q question and regenerate SPARQL (max 1 per sub-Q)
L3: Re-decompose the entire plan with failure history (max 2 plan_attempts)
"""

from __future__ import annotations

import time

from app.core.logger import logger
from app.services.agents.state import State

_MAX_L1_PER_SUBQ = 1
_MAX_L2_PER_SUBQ = 0
_MAX_PLAN_ATTEMPTS = 2


async def repairer_node(state: State) -> dict:
    sub_questions = list(state.get("sub_questions", []))
    scratchpad = dict(state.get("scratchpad", {}))
    plan_attempts = state.get("plan_attempts", 0)
    t0 = time.perf_counter()

    failed = [
        sq for sq in sub_questions
        if scratchpad.get(sq["id"], {}).get("status") == "needs_repair"
    ]

    if not failed:
        step = {"node": "repairer", "label": "No sub-Qs need repair",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"pipeline_steps": state.get("pipeline_steps", []) + [step]}

    updated_sub_questions = [dict(sq) for sq in sub_questions]
    repair_level = "none"

    for sq in failed:
        sqid = sq["id"]
        l1_count = sq.get("l1_count", 0)
        l2_count = sq.get("l2_count", 0)
        result = scratchpad.get(sqid, {})
        error = result.get("reflection", "") or result.get("error", "")

        if l1_count < _MAX_L1_PER_SUBQ:
            logger.info(f"[repairer] L1 repair for {sqid} (attempt {l1_count + 1})")
            for s in updated_sub_questions:
                if s["id"] == sqid:
                    s["status"] = "pending"
                    s["l1_count"] = l1_count + 1
                    s["error"] = error
                    scratchpad[sqid] = {**result, "status": "pending", "sparql_error": error}
            repair_level = "L1"
        elif l2_count < _MAX_L2_PER_SUBQ:
            logger.info(f"[repairer] L2 repair for {sqid} — rewriting sub-Q")
            new_question = sq.get("question", "") + f" (Note: prior attempt failed — {error[:100]}. Try a different SPARQL approach.)"
            for s in updated_sub_questions:
                if s["id"] == sqid:
                    s["question"] = new_question
                    s["status"] = "pending"
                    s["l2_count"] = l2_count + 1
                    scratchpad[sqid] = {**result, "status": "pending"}
            repair_level = "L2"
        else:
            logger.info(f"[repairer] L1/L2 exhausted for {sqid} — marking skipped")
            for s in updated_sub_questions:
                if s["id"] == sqid:
                    s["status"] = "skipped"
            scratchpad[sqid] = {**result, "status": "skipped",
                                "reflection": f"SKIP: L1/L2 repair exhausted. {error}"}

    any_still_pending = any(
        sq["status"] == "pending"
        for sq in updated_sub_questions
        if scratchpad.get(sq["id"], {}).get("status") != "skipped"
    )

    failed_still = [
        sq for sq in updated_sub_questions
        if scratchpad.get(sq["id"], {}).get("status") == "needs_repair"
        and sq.get("l1_count", 0) >= _MAX_L1_PER_SUBQ
        and sq.get("l2_count", 0) >= _MAX_L2_PER_SUBQ
    ]

    if failed_still and plan_attempts < _MAX_PLAN_ATTEMPTS:
        failure_summary = "; ".join(
            f"{sq['id']}: {scratchpad.get(sq['id'], {}).get('reflection', 'unknown error')}"
            for sq in failed_still
        )
        step = {"node": "repairer", "label": "L3: triggering full replan",
                "duration_ms": round((time.perf_counter() - t0) * 1000), "repair_level": "L3"}
        return {
            "sub_questions": updated_sub_questions,
            "scratchpad": scratchpad,
            "halt_reason": f"L3 replan triggered: {failure_summary}",
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    step = {
        "node": "repairer",
        "label": f"Repair dispatched ({repair_level})",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "repair_level": repair_level,
    }
    return {
        "sub_questions": updated_sub_questions,
        "scratchpad": scratchpad,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

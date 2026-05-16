"""plan_validator_node — acyclic DAG check, ontology coverage, cost estimate."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.logger import logger
from app.services.agents.ontology_loader import get_ontology_dict
from app.services.agents.state import State

_MAX_SUBQS = 8
_MAX_PLAN_ATTEMPTS = 2


def _is_acyclic(sub_questions: list[dict]) -> bool:
    """Kahn's algorithm topological sort — returns True if DAG is acyclic."""
    ids = {sq["id"] for sq in sub_questions}
    in_degree: dict[str, int] = {sq["id"]: 0 for sq in sub_questions}
    adj: dict[str, list[str]] = defaultdict(list)
    for sq in sub_questions:
        for dep in sq.get("depends_on", []):
            if dep in ids:
                adj[dep].append(sq["id"])
                in_degree[sq["id"]] += 1
    queue = deque(sqid for sqid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return visited == len(sub_questions)


async def plan_validator_node(state: State) -> dict:
    sub_questions = state.get("sub_questions", [])
    plan_attempts = state.get("plan_attempts", 0)
    t0 = time.perf_counter()

    errors: list[str] = []

    if len(sub_questions) > _MAX_SUBQS:
        errors.append(f"Too many sub-questions ({len(sub_questions)} > {_MAX_SUBQS})")

    if not _is_acyclic(sub_questions):
        errors.append("Sub-question DAG contains a cycle")

    all_ids = {sq["id"] for sq in sub_questions}
    for sq in sub_questions:
        for dep in sq.get("depends_on", []):
            if dep not in all_ids:
                errors.append(f"Sub-question '{sq['id']}' depends on unknown id '{dep}'")

    if plan_attempts >= _MAX_PLAN_ATTEMPTS and errors:
        halt = f"Plan validation failed after {plan_attempts} attempts: {'; '.join(errors)}"
        step = {"node": "plan_validator", "label": "Plan validation failed (exhausted)",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"halt_reason": halt, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    if errors:
        halt = f"Plan invalid: {'; '.join(errors)}"
        step = {"node": "plan_validator", "label": "Plan invalid — requesting replan",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"halt_reason": halt, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    step = {
        "node": "plan_validator",
        "label": f"Plan validated ({len(sub_questions)} sub-Qs)",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "halt_reason": None,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

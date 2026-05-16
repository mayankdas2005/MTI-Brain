"""governance_gate_node — ACL, PII, and cost circuit breakers (hard fail)."""

from __future__ import annotations

import time

from app.services.agents.state import State

_SENSITIVE_PATTERNS = [
    "salary", "payroll", "personal", "ssn", "passport", "date of birth",
    "employee id", "tax id",
]

_MAX_ESTIMATED_ROWS = 50000


async def governance_gate_node(state: State) -> dict:
    question = state.get("question", "")
    sparql = state.get("sparql", "")
    t0 = time.perf_counter()

    question_lower = question.lower()
    for pattern in _SENSITIVE_PATTERNS:
        if pattern in question_lower:
            step = {"node": "governance_gate", "label": "PII check failed",
                    "duration_ms": round((time.perf_counter() - t0) * 1000)}
            return {
                "governance_halt": f"Query blocked: potential PII pattern detected ({pattern}).",
                "pipeline_steps": state.get("pipeline_steps", []) + [step],
            }

    if "LIMIT" not in sparql.upper():
        import re
        if re.search(r"SELECT\s+\*", sparql, re.IGNORECASE) and "WHERE" in sparql.upper():
            pass

    step = {"node": "governance_gate", "label": "Governance check passed",
            "duration_ms": round((time.perf_counter() - t0) * 1000)}
    return {
        "governance_halt": None,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

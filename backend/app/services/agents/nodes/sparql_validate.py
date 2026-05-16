"""sparql_validate_node — deterministic SPARQL syntax + predicate check."""

from __future__ import annotations

import time

from app.services.agents import data_pool as dp
from app.services.agents.state import State
from app.services.agents.validators import validate_sparql_syntax, validate_predicates


async def sparql_validate_node(state: State) -> dict:
    sparql = state.get("sparql", "")
    t0 = time.perf_counter()

    ok, err = validate_sparql_syntax(sparql)
    if not ok:
        step = {"node": "sparql_validate", "label": "SPARQL validation (syntax fail)",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"sparql_error": err, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    try:
        client = dp.get_kg_client()
        ok2, err2 = await validate_predicates(sparql, client)
        if not ok2:
            step = {"node": "sparql_validate", "label": "SPARQL validation (predicate fail)",
                    "duration_ms": round((time.perf_counter() - t0) * 1000)}
            return {"sparql_error": err2, "pipeline_steps": state.get("pipeline_steps", []) + [step]}
    except Exception as e:
        from app.core.logger import logger
        logger.warning(f"Predicate validation skipped: {e}")

    step = {"node": "sparql_validate", "label": "SPARQL validated",
            "duration_ms": round((time.perf_counter() - t0) * 1000)}
    return {"sparql_error": "", "pipeline_steps": state.get("pipeline_steps", []) + [step]}

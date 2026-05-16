"""sparql_execute_node — run SPARQL SELECT against Jena Fuseki."""

from __future__ import annotations

import time

from app.core.logger import logger
from app.services.agents import data_pool as dp
from app.services.agents.state import State


async def sparql_execute_node(state: State) -> dict:
    sparql = state.get("sparql", "")
    t0 = time.perf_counter()

    try:
        client = dp.get_kg_client()
        columns, rows, raw_bindings = await client.execute_select(sparql)
    except Exception as e:
        err = str(e)
        logger.warning(f"SPARQL execution error: {err}")
        step = {"node": "sparql_execute", "label": "SPARQL execution failed",
                "duration_ms": round((time.perf_counter() - t0) * 1000), "error": err}
        return {
            "sparql_error": f"Execution error: {err}",
            "kg_results": [],
            "kg_columns": [],
            "kg_rows": [],
            "kg_row_count": 0,
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    step = {
        "node": "sparql_execute",
        "label": "SPARQL executed",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "row_count": len(rows),
    }
    return {
        "sparql_error": "",
        "kg_results": raw_bindings,
        "kg_columns": columns,
        "kg_rows": rows,
        "kg_row_count": len(rows),
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

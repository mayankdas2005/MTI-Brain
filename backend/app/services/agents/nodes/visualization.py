"""visualization_node — populate chart spec with data (deterministic, no LLM)."""

from __future__ import annotations

import time

from backend.app.services.neo4j_analytics.helpers import _build_chart_data
from app.services.agents.state import State


async def visualization_node(state: State) -> dict:
    chart_json = state.get("chart_json") or {}
    kg_columns = state.get("kg_columns", [])
    kg_rows = state.get("kg_rows", [])
    t0 = time.perf_counter()

    if not chart_json or not chart_json.get("type") or not kg_columns or not kg_rows:
        step = {"node": "visualization", "label": "No chart applicable",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"viz_spec": None, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    viz_spec = _build_chart_data(chart_json, kg_columns, kg_rows)

    step = {
        "node": "visualization",
        "label": "Building chart",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "chart_type": viz_spec.get("type") if viz_spec else None,
    }
    return {
        "viz_spec": viz_spec or None,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

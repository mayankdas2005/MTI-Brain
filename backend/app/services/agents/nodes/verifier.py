"""verifier_node — cardinality, units, and threshold sanity checks."""

from __future__ import annotations

import time

from app.services.agents.state import State

_INTENT_ROW_RANGES: dict[str, tuple[int, int]] = {
    "balance_lookup": (0, 50),
    "counterparty_exposure": (0, 30),
    "fx_exposure": (0, 200),
    "investment_positions": (0, 5000),
    "maturity_ladder": (0, 100),
    "policy_check": (0, 20),
    "code_lookup": (0, 10),
    "trend_analysis": (0, 500),
    "scenario_forecast": (0, 200),
    "multi_entity_join": (0, 1000),
}

_MONETARY_KEYWORDS = {"amount", "value", "marketvalue", "faceamount", "mtmamount", "notionalamount", "bookvalue"}


def _check_monetary_sanity(columns: list[str], rows: list[list]) -> str | None:
    """Flag if ALL values in a monetary column are negative (likely wrong sign)."""
    for i, col in enumerate(columns):
        if col.lower() in _MONETARY_KEYWORDS:
            vals = [r[i] for r in rows if r[i] is not None]
            if not vals:
                continue
            try:
                nums = [float(v) for v in vals]
                if all(n < 0 for n in nums) and len(nums) > 1:
                    return f"All values in '{col}' are negative — query may have wrong sign."
            except (TypeError, ValueError):
                pass
    return None


async def verifier_node(state: State) -> dict:
    intent = state.get("intent", "")
    kg_columns = state.get("kg_columns", [])
    kg_rows = state.get("kg_rows", [])
    row_count = state.get("kg_row_count", 0)
    t0 = time.perf_counter()

    step_base = {"node": "verifier", "duration_ms": 0}

    min_rows, max_rows = _INTENT_ROW_RANGES.get(intent, (0, 10000))
    if row_count > max_rows:
        step = {**step_base, "label": "Verifier: row count exceeded",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {
            "sparql_error": f"Result has {row_count} rows (max expected {max_rows} for intent '{intent}'). SPARQL may be missing a FILTER.",
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    monetary_issue = _check_monetary_sanity(kg_columns, kg_rows)
    if monetary_issue:
        step = {**step_base, "label": "Verifier: monetary sanity fail",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {
            "sparql_error": monetary_issue,
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    step = {**step_base, "label": "Verifier: passed",
            "duration_ms": round((time.perf_counter() - t0) * 1000)}
    return {
        "sparql_error": "",
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

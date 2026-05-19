"""verifier_node — rule-based sanity checks + LLM semantic verification."""

from __future__ import annotations

import time

from app.services.agents.bedrock import get_llm
from app.services.agents.prompts import VERIFIER_PROMPT
from app.services.agents.state import State

_MAX_SPARQL_RETRIES = 2  # keep in sync with MAX_SPARQL_RETRIES in graph.py

_INTENT_ROW_RANGES: dict[str, tuple[int, int]] = {
    "balance_lookup": (0, 50),
    "balance_and_policy": (0, 50),
    "exposure_analysis": (0, 200),
    "counterparty_exposure": (0, 30),
    "fx_exposure": (0, 200),
    "investment_positions": (0, 5000),
    "investment_and_maturity": (0, 5000),
    "maturity_ladder": (0, 100),
    "policy_check": (0, 20),
    "code_lookup": (0, 10),
    "trend_analysis": (0, 500),
    "trend_and_forecast": (0, 500),
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


def _format_sample(columns: list[str], rows: list[list], limit: int = 10) -> str:
    if not columns or not rows:
        return "No results."
    header = " | ".join(columns)
    lines = [header]
    for row in rows[:limit]:
        lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
    return "\n".join(lines)


async def verifier_node(state: State) -> dict:
    intent = state.get("intent", "")
    kg_columns = state.get("kg_columns", [])
    kg_rows = state.get("kg_rows", [])
    row_count = state.get("kg_row_count", 0)
    question = state.get("question", "")
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

    if row_count == 0 and state.get("sparql_retries", 0) < _MAX_SPARQL_RETRIES:
        step = {**step_base, "label": "Verifier: 0 rows — triggering guided retry",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        sparql_text = state.get("sparql", "")
        has_having = "HAVING" in sparql_text.upper()
        if has_having:
            error_msg = (
                "Query executed successfully but returned 0 rows. "
                "The query uses HAVING — the most likely cause is that the threshold filters all groups "
                "(i.e., no entity in the data meets the condition). "
                "Do NOT change predicates, graphs, or dates. "
                "Instead: verify data exists by removing the HAVING clause entirely and re-running. "
                "If removing HAVING returns data, output the query WITHOUT HAVING so the user sees all "
                "entities ranked by variance — 0 rows with HAVING is valid when no anomaly exceeds the threshold."
            )
        else:
            error_msg = (
                "Query executed successfully but returned 0 rows. "
                "Likely causes: "
                "(1) wrong predicate — e.g. used lpp:sourceAccount instead of lpp:forAccount for balance snapshots; "
                "(2) missing named graph — include FROM <graph:treasury:all> for treasury data, "
                "FROM <graph:fx:current> for FX, FROM <graph:investments:all> for investments; "
                "(3) date literal mismatch — use exact xsd:date literals, never SPARQL date arithmetic. "
                "Regenerate with corrected predicates, the correct FROM clause, and literal date values."
            )
        return {
            "sparql_error": error_msg,
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    llm = get_llm("fast")
    raw = await (VERIFIER_PROMPT | llm).ainvoke(
        {
            "question": question,
            "intent": intent,
            "columns": ", ".join(kg_columns) if kg_columns else "none",
            "row_count": row_count,
            "sample": _format_sample(kg_columns, kg_rows),
        },
        config={"tags": ["no_stream"]},
    )
    verdict = (raw.content if hasattr(raw, "content") else str(raw)).strip()

    if verdict.startswith("FAIL"):
        step = {**step_base, "label": "Verifier: semantic check failed",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        reason = verdict[len("FAIL:"):].strip() if ":" in verdict else verdict
        return {
            "sparql_error": f"Verifier: {reason}",
            "pipeline_steps": state.get("pipeline_steps", []) + [step],
        }

    step = {**step_base, "label": "Verifier: passed",
            "duration_ms": round((time.perf_counter() - t0) * 1000)}
    return {
        "sparql_error": "",
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

"""Node 3: executor — runs SQL on Redshift, handles zero-row probing, repair, and audit logging.

Implementation is split across:
  repair.py         — LLM-based SQL repair (_attempt_repair)
  zero_row_probe.py — 3-stage zero-row diagnosis
  audit.py          — write_audit_log, write_query_pattern, write_anti_pattern
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.nodes.audit import write_audit_log, write_query_pattern
from app.services.neo4j_analytics.nodes.repair import attempt_repair
from app.services.neo4j_analytics.nodes.zero_row_probe import zero_row_probe
from app.services.neo4j_analytics.result_summarizer import summarize_results
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState


async def executor(state: AnalyticsState, config: RunnableConfig) -> dict:
    sql_list = state.get("sql_list", [])
    ir_list = state.get("semantic_ir_list", [])
    repair_count = state.get("repair_count", 0)
    logger.info("executor START | thread={} | sql_count={} | repair_count={}", state["thread_id"], len(sql_list), repair_count)

    if not sql_list or not ir_list:
        logger.warning("executor | no SQL to execute | thread={}", state["thread_id"])
        return {"error": "No SQL available to execute.", "no_data": True}

    query_timeout = 300
    result_list = []
    all_columns = []
    all_rows = []
    reliability_flags = list(state.get("reliability_flags") or [])
    max_rows = state.get("max_rows", 100)

    if len(sql_list) > 1:
        logger.warning(
            "executor | sql_list has {} entries — expected 1 (decomposition removed) | using first only | thread={}",
            len(sql_list), state["thread_id"],
        )
        sql_list = [sql_list[0]]
        ir_list = [ir_list[0]]

    logger.info("executor | running SQL | thread={} | sql_preview={}", state["thread_id"], sql_list[0][:400])
    try:
        res = await _execute_single(sql_list[0], ir_list[0], state, query_timeout, max_rows)
        result_list = [{"index": 0, **res}]
        all_columns = res.get("columns", [])
        all_rows = res.get("rows", [])
        logger.info("executor | SQL result | thread={} | rows={} | columns={}", state["thread_id"], len(all_rows), all_columns)
    except Exception as e:
        logger.error("executor | SQL execution failed | thread={} | error={}", state["thread_id"], e)
        result_list = [{"index": 0, "error": str(e), "columns": [], "rows": []}]

    all_errors = [r["error"] for r in result_list if r.get("error")]
    first_ir = SemanticIR(**ir_list[0]) if ir_list else None

    if all_errors and repair_count < 2:
        repair_result = await attempt_repair(
            state, sql_list, ir_list, all_errors, repair_count, config,
            schema_context=state.get("semantic_context") or {},
        )
        if repair_result:
            return repair_result

    if all_errors:
        partial_rows = []
        partial_cols: list = []
        for r in result_list:
            if r.get("rows"):
                partial_rows.extend(r["rows"])
            if not partial_cols and r.get("columns"):
                partial_cols = r["columns"]

        if partial_rows:
            logger.warning(
                "executor | repairs exhausted but partial data available | thread={} | rows={} | failed_errors={}",
                state["thread_id"], len(partial_rows),
                [r.get("error") for r in result_list if r.get("error")],
            )
            all_rows = partial_rows
            all_columns = partial_cols
            all_errors = []
        else:
            combined_error = "; ".join(all_errors[:3])
            logger.warning("executor | repairs exhausted, no usable data | thread={} | error={}", state["thread_id"], combined_error)
            asyncio.create_task(write_audit_log(state, sql_list[0] if sql_list else "", 0, "failed"))
            return {
                "result_list": result_list,
                "error": combined_error,
                "execution_error": combined_error,
                "no_data": True,
                "repair_count": repair_count,
                "_prev_repair_count": repair_count,
            }

    total_rows = len(all_rows)

    if total_rows == 0 and not all_errors:
        probe_result = await zero_row_probe(first_ir, state)
        if probe_result.get("needs_clarification"):
            return {
                "result_list": result_list,
                "no_data": True,
                "zero_row_probe_result": probe_result.get("reason"),
                "needs_clarification": True,
                "clarification_reason": probe_result.get("reason"),
                "_prev_repair_count": repair_count,
            }
        return {
            "result_list": result_list,
            "no_data": True,
            "zero_row_probe_result": probe_result.get("reason"),
            "_prev_repair_count": repair_count,
        }

    reliability_flags = _check_reliability(first_ir, all_rows, reliability_flags)

    query_summary = summarize_results(
        columns=all_columns,
        rows=all_rows,
        intent=first_ir.intent if first_ir else "",
        reliability_flags=reliability_flags,
    )

    asyncio.create_task(write_audit_log(state, sql_list[0] if sql_list else "", total_rows, "success"))
    if not all_errors and total_rows > 0:
        asyncio.create_task(write_query_pattern(state, sql_list[0] if sql_list else "", first_ir))

    logger.info(
        "executor DONE | thread={} | total_rows={} | columns={} | no_data=False | flags={} | passing to synthesis",
        state["thread_id"], total_rows, all_columns, reliability_flags,
    )
    return {
        "result_list": result_list,
        "query_summary": query_summary.model_dump(),
        "no_data": False,
        "reliability_flags": reliability_flags,
        "error": None,
        "execution_error": None,
        "_prev_repair_count": repair_count,
        "zero_row_probe_result": None,
        "needs_clarification": False,
        "clarification_reason": None,
    }


async def _execute_single(sql: str, ir_dict: dict, state: AnalyticsState, timeout_s: int, max_rows: int = 100) -> dict:
    from app.services.neo4j_analytics.redshift_client import execute_query

    bounded_sql = _apply_row_limit(sql, max_rows)
    columns, rows = await execute_query(bounded_sql, timeout_s=timeout_s, thread_id=state["thread_id"])
    rows = _make_rows_json_safe(rows[:max_rows])
    return {"columns": columns, "rows": rows, "cached": False}


def _apply_row_limit(sql: str, max_rows: int) -> str:
    """Inject or tighten the LIMIT clause in the SQL.

    Only touches the outermost LIMIT — never modifies subqueries.
    """
    import re
    limit_pattern = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)
    match = limit_pattern.search(sql.rstrip())
    if match:
        existing = int(match.group(1))
        if existing <= max_rows:
            return sql
        return limit_pattern.sub(f"LIMIT {max_rows}", sql.rstrip())
    return sql.rstrip() + f"\nLIMIT {max_rows}"


def _make_rows_json_safe(rows: list[list]) -> list[list]:
    """Convert datetime.date and Decimal values to JSON-serializable types."""
    import datetime
    import decimal
    safe = []
    for row in rows:
        safe_row = []
        for v in row:
            if isinstance(v, datetime.datetime):
                safe_row.append(v.isoformat())
            elif isinstance(v, datetime.date):
                safe_row.append(v.isoformat())
            elif isinstance(v, decimal.Decimal):
                safe_row.append(float(v))
            else:
                safe_row.append(v)
        safe.append(safe_row)
    return safe


def _check_reliability(ir: SemanticIR | None, rows: list, flags: list[str]) -> list[str]:
    if not ir or not rows:
        return flags

    intent = (ir.intent or "").lower()

    if "kpi" in intent or "total" in intent:
        if len(rows) > 50:
            if "unexpected_row_count" not in flags:
                flags.append("unexpected_row_count")

    if "trend" in intent or "over time" in intent:
        if len(rows) == 1:
            if "trend_insufficient_data" not in flags:
                flags.append("trend_insufficient_data")

    return flags

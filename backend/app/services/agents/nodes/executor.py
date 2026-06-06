"""Node 3: executor — runs SQL on Redshift, handles zero-row probing, repair, and audit logging.

Implementation is split across:
  repair.py         — LLM-based SQL repair (_attempt_repair)
  zero_row_probe.py — 3-stage zero-row diagnosis
  audit.py          — write_audit_log, write_query_pattern (called from pipeline.py), write_anti_pattern
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.nodes.audit import write_audit_log
from app.services.agents.nodes.repair import attempt_repair
from app.services.agents.nodes.zero_row_probe import zero_row_probe
from app.services.agents.result_summarizer import summarize_results
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


async def executor(state: AnalyticsState, config: RunnableConfig) -> dict:
    sql_list = state.get("sql_list", [])
    ir_list = state.get("semantic_ir_list", [])
    repair_count = state.get("repair_count", 0)
    logger.info("executor START | thread={} | sql_count={} | repair_count={}", state["thread_id"], len(sql_list), repair_count)

    if not sql_list or not ir_list:
        logger.warning("executor | no SQL to execute | thread={}", state["thread_id"])
        return {"error": "No SQL available to execute.", "no_data": True}

    query_timeout = 180
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
                "zero_row_probe_result": f"Query failed after {repair_count} repair attempt(s). Last error: {combined_error[:300]}",
            }

    total_rows = len(all_rows)

    if total_rows == 0 and not all_errors:
        probe_result = await zero_row_probe(first_ir, state)
        probe_type = probe_result.get("probe_type", "unknown")
        zero_row_rewrite_count = state.get("zero_row_rewrite_count", 0)

        if probe_type == "bad_join" and zero_row_rewrite_count == 0 and repair_count < 2:
            logger.info(
                "executor | zero-row bad join — attempting SQL repair | thread={}", state["thread_id"]
            )
            repair_result = await attempt_repair(
                state, sql_list, ir_list,
                [probe_result.get("reason", "The join produces 0 rows — fix the ON clause using candidate join paths.")],
                repair_count, config,
                schema_context=state.get("semantic_context") or {},
            )
            if repair_result:
                return {**repair_result, "zero_row_rewrite_count": 1}

        # Deterministic filter retry: strip the problematic filter(s) and re-execute.
        # probe_type tells us exactly which strategy to use — max 1 extra Redshift query.
        _RETRY_PROBE_TYPES = frozenset({"time_filter", "filter_mismatch", "filter_combo"})
        if probe_type in _RETRY_PROBE_TYPES and zero_row_rewrite_count == 0:
            relaxed_sql, retry_flag = _build_zero_row_retry_sql(sql_list[0], first_ir, probe_type)
            if relaxed_sql:
                try:
                    relaxed = await _execute_single(relaxed_sql, ir_list[0], state, query_timeout, max_rows)
                    relaxed_rows = relaxed.get("rows", [])
                    if relaxed_rows:
                        relaxed_summary = summarize_results(
                            columns=relaxed.get("columns", []),
                            rows=relaxed_rows,
                            intent=first_ir.intent if first_ir else "",
                            reliability_flags=[retry_flag],
                        )
                        logger.info(
                            "executor | zero-row retry OK | probe={} | flag={} | rows={} | thread={}",
                            probe_type, retry_flag, len(relaxed_rows), state["thread_id"],
                        )
                        asyncio.create_task(write_audit_log(state, relaxed_sql, len(relaxed_rows), "success"))
                        return {
                            "result_list": [{"index": 0, **relaxed}],
                            "query_summary": relaxed_summary.model_dump(),
                            "no_data": False,
                            "zero_row_probe_result": probe_result.get("reason"),
                            "reliability_flags": [retry_flag],
                            "error": None,
                            "execution_error": None,
                            "_prev_repair_count": repair_count,
                            "zero_row_rewrite_count": 1,
                            "needs_clarification": False,
                            "clarification_reason": None,
                        }
                except Exception as e:
                    logger.warning(
                        "executor | zero-row retry failed | probe={} | error={}", probe_type, e
                    )

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
    from app.services.agents.redshift_client import execute_query

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


# ── Zero-row retry helpers ────────────────────────────────────────────────────

def _build_zero_row_retry_sql(sql: str, ir: SemanticIR | None, probe_type: str) -> tuple[str | None, str]:
    """Return (relaxed_sql, reliability_flag) for the given probe_type.

    time_filter  → strip only the time_filter column predicates (surgical)
    filter_mismatch / filter_combo → strip all WHERE/HAVING (broad)
    Falls back to broad strip if surgical strip fails or time_filter is not set.
    """
    if probe_type == "time_filter" and ir and ir.time_filter:
        stripped = _strip_col_filter_sql(sql, ir.time_filter.column_name)
        if stripped:
            return stripped, "time_filter_relaxed"
    stripped = _strip_all_where_filters(sql)
    return stripped, "filters_relaxed"


def _strip_col_filter_sql(sql: str, col_name: str) -> str | None:
    """Remove all predicates referencing col_name from every WHERE clause in the SQL."""
    import sqlglot
    import sqlglot.expressions as exp
    try:
        stmt = sqlglot.parse_one(sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE)
        if stmt is None:
            return None
        for sel in stmt.find_all(exp.Select):
            where = sel.args.get("where")
            if not where:
                continue
            new_cond = _drop_col_predicates(where.this, col_name)
            sel.set("where", exp.Where(this=new_cond) if new_cond is not None else None)
        return stmt.sql(dialect="redshift", pretty=True)
    except Exception:
        return None


def _strip_all_where_filters(sql: str) -> str | None:
    """Remove all WHERE and HAVING clauses from every SELECT in the SQL."""
    import sqlglot
    import sqlglot.expressions as exp
    try:
        stmt = sqlglot.parse_one(sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE)
        if stmt is None:
            return None
        for sel in stmt.find_all(exp.Select):
            sel.set("where", None)
            sel.set("having", None)
        return stmt.sql(dialect="redshift", pretty=True)
    except Exception:
        return None


def _drop_col_predicates(node, col_name: str):
    """Walk AND/OR predicate tree; remove any leaf that references col_name.

    Returns None when the entire node should be dropped.
    """
    import sqlglot.expressions as exp
    if node is None:
        return None
    # Leaf predicates: drop if they reference the target column
    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In)):
        if any(c.name == col_name for c in node.find_all(exp.Column)):
            return None
        return node
    # AND: propagate drops — if one arm drops, return the other
    if isinstance(node, exp.And):
        L = _drop_col_predicates(node.left, col_name)
        R = _drop_col_predicates(node.right, col_name)
        if L is None and R is None:
            return None
        return R if L is None else (L if R is None else exp.And(this=L, expression=R))
    # OR: same treatment
    if isinstance(node, exp.Or):
        L = _drop_col_predicates(node.left, col_name)
        R = _drop_col_predicates(node.right, col_name)
        if L is None and R is None:
            return None
        return R if L is None else (L if R is None else exp.Or(this=L, expression=R))
    return node

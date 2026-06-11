"""Node V: sql_validator — deterministic AST validation on generated SQL.

No LLM. Applies 4 gates, routes to recompile (max 1) or error_response on second failure.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import re as _re

from app.core.logger import logger
from app.services.agents.helpers import format_sql
from app.services.agents.sql_validator_logic import try_fix_cte_refs, validate_sql, validate_column_names, validate_filter_types
from app.services.agents.state import AnalyticsState


def _extract_root_cost(text: str) -> float:
    """Extract the upper bound cost estimate from the first EXPLAIN cost= line.

    Pure string split — no regex.
    """
    for line in text.split("\n"):
        if "cost=" in line:
            after_cost = line.split("cost=", 1)[1]
            range_part = after_cost.split(" ")[0]
            upper = range_part.split("..")[-1]
            try:
                return float(upper)
            except ValueError:
                continue
    return 0.0


def _parse_explain_flags(text: str, cost_threshold: float) -> list[str]:
    """Parse Redshift EXPLAIN output for performance anti-patterns.

    Pure string search — no regex.
    Returns a list of flag strings; empty list means no issues detected.
    """
    flags: list[str] = []
    for line in text.split("\n"):
        if "DS_BCAST_INNER" in line and "CROSS_JOIN_BROADCAST" not in flags:
            flags.append("CROSS_JOIN_BROADCAST")
        if "DS_DIST_BOTH" in line and "DIST_BOTH" not in flags:
            flags.append("DIST_BOTH")
        if "DS_DIST_OUTER" in line and "DIST_OUTER" not in flags:
            flags.append("DIST_OUTER")
        if "Nested Loop Join in the query plan" in line and "CARTESIAN_RISK" not in flags:
            flags.append("CARTESIAN_RISK")
    for line in text.split("\n"):
        if "Seq Scan on" in line and "cost=" in line and "LARGE_TABLE_SCAN" not in flags:
            if _extract_root_cost(line) > cost_threshold:
                flags.append("LARGE_TABLE_SCAN")
    return flags


async def sql_validator(state: AnalyticsState, config: RunnableConfig) -> dict:
    sql_list = state.get("sql_list", [])
    recompile_count = state.get("recompile_count", 0)
    logger.info("sql_validator START | thread={} | sql_count={} | recompile_count={}", state["thread_id"], len(sql_list), recompile_count)

    if not sql_list:
        logger.warning("sql_validator | no SQL to validate | thread={}", state["thread_id"])
        return {"error": "No SQL was generated to validate.", "recompile_count": recompile_count + 1, "failed_sql_indices": []}

    errors = []
    failed_indices = []
    fixed_sql_list = list(sql_list)
    auto_fixed = False

    for i, sql in enumerate(fixed_sql_list):
        if not sql.strip():
            errors.append(f"SQL #{i+1} is empty")
            failed_indices.append(i)
            continue
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            fixed = try_fix_cte_refs(sql)
            if fixed:
                is_valid2, error_msg2 = validate_sql(fixed)
                if is_valid2:
                    fixed_sql_list[i] = format_sql(fixed)
                    auto_fixed = True
                    logger.info("sql_validator | auto-fixed CTE table qualifiers | index={} | thread={}", i, state["thread_id"])
                    continue
                errors.append(f"SQL #{i+1}: {error_msg2}")
            else:
                errors.append(f"SQL #{i+1}: {error_msg}")
            failed_indices.append(i)

    if not errors:
        # Gate 5 — schema-aware column validation (only runs when Gates 1-3.6 pass)
        # Use _column_lookup (full Neo4j data) for validation — same source as ir_validation.
        # Using display columns (trimmed) causes false negatives: ir_validation allows a column
        # that the display set doesn't include, then sql_validator rejects it, burning a repair.
        sc = state.get("semantic_context") or {}
        col_lookup_keys = sc.get("_column_lookup") or {}
        if col_lookup_keys:
            # Build schema_cols list from _column_lookup for Gate 5
            schema_cols = [{"table_fqn": tfqn, "name": cname}
                           for (tfqn, cname) in col_lookup_keys]
        else:
            schema_cols = sc.get("columns", [])
        for i, sql in enumerate(fixed_sql_list):
            if i in failed_indices:
                continue
            col_ok, col_err = validate_column_names(sql, schema_cols)
            if not col_ok:
                errors.append(f"SQL #{i+1}: {col_err}")
                failed_indices.append(i)

    if not errors:
        # Gate 6 — filter type validation: boolean columns must use TRUE/FALSE, not string literals
        schema_cols = (state.get("semantic_context") or {}).get("columns", [])
        for i, sql in enumerate(fixed_sql_list):
            if i in failed_indices:
                continue
            type_ok, type_err = validate_filter_types(sql, schema_cols)
            if not type_ok:
                errors.append(f"SQL #{i+1}: {type_err}")
                failed_indices.append(i)

    if not errors:
        logger.info("sql_validator DONE | thread={} | all {} SQL(s) valid | auto_fixed={}", state["thread_id"], len(fixed_sql_list), auto_fixed)
        result: dict = {"error": None, "failed_sql_indices": []}
        if auto_fixed:
            result["sql_list"] = fixed_sql_list

        # Structural warnings — non-blocking, added to reliability_flags so synthesis caveats them.
        new_flags: list[str] = list(state.get("reliability_flags") or [])
        col_lookup = {
            (c.get("table_fqn"), c.get("name")): c
            for c in (state.get("semantic_context") or {}).get("columns", [])
            if c.get("table_fqn") and c.get("name")
        }
        ir_list = state.get("semantic_ir_list") or []
        join_clauses = ir_list[0].get("join_clauses", []) if ir_list else []

        for sql in fixed_sql_list:
            # Gate A: LIMIT without ORDER BY — non-deterministic row selection
            has_limit = bool(_re.search(r'\bLIMIT\b', sql, _re.IGNORECASE))
            has_order = bool(_re.search(r'\bORDER\s+BY\b', sql, _re.IGNORECASE))
            if has_limit and not has_order and "limit_without_order" not in new_flags:
                new_flags.append("limit_without_order")
                logger.debug("sql_validator | flag=limit_without_order | thread={}", state["thread_id"])

        # Gate B: NULL-able join keys — uses IR join_clauses + column_lookup, no SQLGlot
        for clause in (join_clauses or []):
            if not clause:
                continue
            sides = clause.split("=")
            if len(sides) != 2:
                continue
            for side in sides:
                parts = side.strip().split(".")
                if len(parts) == 3:
                    fqn = f"{parts[0]}.{parts[1]}"
                    col = parts[2]
                    meta = col_lookup.get((fqn, col), {})
                    null_frac = meta.get("null_frac") or 0.0
                    if null_frac > 0.05 and "nullable_join_key" not in new_flags:
                        new_flags.append("nullable_join_key")
                        logger.debug(
                            "sql_validator | flag=nullable_join_key | {}.{} null_frac={:.0%} | thread={}",
                            fqn, col, null_frac, state["thread_id"],
                        )

        if new_flags != list(state.get("reliability_flags") or []):
            result["reliability_flags"] = new_flags

        # Phase 2: EXPLAIN — only runs when all static gates pass
        from app.services.agents.redshift_client import run_explain as _run_explain
        from app.core.config import settings as _settings
        _cost_threshold = float(
            getattr(getattr(_settings, "sql_validator", None), "explain_cost_threshold", None) or 10_000_000
        )
        _sql_to_explain = fixed_sql_list[0] if fixed_sql_list else ""
        if _sql_to_explain:
            _explain_lines = await _run_explain(_sql_to_explain)
            _explain_text = "\n".join(_explain_lines)
            _explain_cost = _extract_root_cost(_explain_text)
            _explain_flags = _parse_explain_flags(_explain_text, _cost_threshold)
            if _explain_flags:
                logger.warning(
                    "sql_validator | EXPLAIN flags={} | cost={:.0f} | thread={}",
                    _explain_flags, _explain_cost, state["thread_id"],
                )
            else:
                logger.info(
                    "sql_validator | EXPLAIN clean | cost={:.0f} | thread={}",
                    _explain_cost, state["thread_id"],
                )
            result["explain_cost"] = _explain_cost
            result["explain_flags"] = _explain_flags
            result["explain_output"] = _explain_text
            result["needs_performance_repair"] = bool(_explain_flags)

        return result

    combined_error = "; ".join(errors)
    logger.warning("sql_validator | validation failed | thread={} | failed_indices={} | error={}", state["thread_id"], failed_indices, combined_error)

    return {"error": combined_error, "recompile_count": recompile_count + 1, "failed_sql_indices": failed_indices}


def is_safe_count_query(sql: str) -> bool:
    """Validate that a LLM-generated probe SQL is a safe COUNT(*) query.

    Used by zero_row_probe to guard against executing malformed or unsafe probe SQL.
    Returns True if the SQL looks like a safe COUNT query, False otherwise.
    """
    if not sql or not sql.strip():
        return False
    sql_upper = sql.strip().upper()
    # Must start with SELECT or WITH (CTEs allowed)
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return False
    # Must not contain destructive statements
    _DANGEROUS = ("DROP ", "DELETE ", "TRUNCATE ", "INSERT ", "UPDATE ", "ALTER ", "CREATE ", "GRANT ", "REVOKE ")
    if any(d in sql_upper for d in _DANGEROUS):
        return False
    # Should contain COUNT — probe SQL should be a count query
    if "COUNT" not in sql_upper:
        return False
    return True

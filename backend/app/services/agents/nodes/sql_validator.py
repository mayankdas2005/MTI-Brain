"""Node V: sql_validator — deterministic AST validation on generated SQL.

No LLM. Applies 4 gates, routes to recompile (max 1) or error_response on second failure.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.sql_validator_logic import try_fix_cte_refs, validate_sql, validate_column_names, validate_filter_types
from app.services.agents.state import AnalyticsState


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
                    fixed_sql_list[i] = fixed
                    auto_fixed = True
                    logger.info("sql_validator | auto-fixed CTE table qualifiers | index={} | thread={}", i, state["thread_id"])
                    continue
                errors.append(f"SQL #{i+1}: {error_msg2}")
            else:
                errors.append(f"SQL #{i+1}: {error_msg}")
            failed_indices.append(i)

    if not errors:
        # Gate 5 — schema-aware column validation (only runs when Gates 1-3.6 pass)
        schema_cols = (state.get("semantic_context") or {}).get("columns", [])
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

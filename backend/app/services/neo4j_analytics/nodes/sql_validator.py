"""Node V: sql_validator — deterministic AST validation on generated SQL.

No LLM. Applies 4 gates, routes to recompile (max 1) or error_response on second failure.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.sql_validator_logic import try_fix_cte_refs, validate_sql
from app.services.neo4j_analytics.state import AnalyticsState


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
            errors.append(f"SQL #{i+1}: {error_msg}")
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

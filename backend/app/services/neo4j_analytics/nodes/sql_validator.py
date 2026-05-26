"""Node V: sql_validator — deterministic AST validation on generated SQL.

No LLM. Applies 4 gates, routes to recompile (max 1) or error_response on second failure.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.sql_validator_logic import validate_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def sql_validator(state: AnalyticsState, config: RunnableConfig) -> dict:
    sql_list = state.get("sql_list", [])
    recompile_count = state.get("recompile_count", 0)
    logger.info("sql_validator START | thread={} | sql_count={} | recompile_count={}", state["thread_id"], len(sql_list), recompile_count)

    if not sql_list:
        logger.warning("sql_validator | no SQL to validate | thread={}", state["thread_id"])
        return {"error": "No SQL was generated to validate.", "recompile_count": recompile_count}

    errors = []
    for i, sql in enumerate(sql_list):
        if not sql.strip():
            errors.append(f"SQL #{i+1} is empty")
            continue
        is_valid, error_msg = validate_sql(sql)
        if not is_valid:
            errors.append(f"SQL #{i+1}: {error_msg}")

    if not errors:
        logger.info("sql_validator DONE | thread={} | all {} SQL(s) valid", state["thread_id"], len(sql_list))
        return {"error": None}

    combined_error = "; ".join(errors)
    logger.warning("sql_validator | validation failed | thread={} | error={}", state["thread_id"], combined_error)

    return {"error": combined_error, "recompile_count": recompile_count}

"""Node: sql_generator — LLM SQL generation from SemanticIR.

Single responsibility: take semantic_ir_list from state, generate SQL for each IR
via LLM, write sql_list back to state.

Column validation (strip_hallucinated_columns + validate_and_fix_join_clauses) is
performed upstream in query_compiler immediately after build_semantic_ir so the IR
reaching here already has validated column names and join clauses.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.nodes.sql_generator import generate_sql_llm
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


async def sql_generator(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("sql_generator START | thread={}", state["thread_id"])

    ir_list = state.get("semantic_ir_list") or []
    semantic_context = state.get("semantic_context") or {}

    if not ir_list:
        logger.warning("sql_generator | no IR list | thread={}", state["thread_id"])
        return {"sql_list": [], "error": "No IR to generate SQL from"}

    sql_list: list[str] = []

    for ir_dict in ir_list:
        ir = SemanticIR(**ir_dict)
        try:
            sql = await generate_sql_llm(ir, semantic_context, state, config)
            if not sql:
                raise ValueError("LLM returned empty SQL")
            sql_list.append(sql)
        except Exception as e:
            logger.error("sql_generator | LLM SQL generation failed | thread={} | error={}", state["thread_id"], e)
            sql_list.append("")

    first_sql = next((s for s in sql_list if s), None)
    logger.info("sql_generator DONE | thread={} | sql_len={}", state["thread_id"], len(first_sql or ""))

    # Y3: sql_computation_summary — tell synthesis which decision computations are in the SQL
    _implemented: list[str] = []
    if first_sql:
        _sql_upper = first_sql.upper()
        if "THRESHOLD_BREACH_FLAG" in _sql_upper or ("BREACH" in _sql_upper and "CASE WHEN" in _sql_upper):
            _implemented.append("threshold_breach_flag")
        if "RUNNING_" in _sql_upper and " OVER " in _sql_upper:
            _implemented.append("running_total")
        if "DELTA_" in _sql_upper:
            _implemented.append("delta")
        if "PERIOD_CHANGE_" in _sql_upper:
            _implemented.append("period_change")
        if "YOY" in _sql_upper or "DATEADD(YEAR" in _sql_upper or "DATEADD(YEAR," in _sql_upper:
            _implemented.append("yoy_baseline")

    result: dict = {"sql_list": sql_list, "semantic_context": semantic_context, "sql_computation_summary": _implemented}
    if first_sql:
        result["prior_sql"] = first_sql
    return result

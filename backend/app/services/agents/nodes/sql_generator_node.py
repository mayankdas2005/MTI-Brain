"""Node: sql_generator — LLM SQL generation from SemanticIR.

Single responsibility: take semantic_ir_list from state, generate SQL for each IR
via LLM, write sql_list back to state.
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

    sql_list = []
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

    result: dict = {"sql_list": sql_list}
    if first_sql:
        result["prior_sql"] = first_sql
    return result

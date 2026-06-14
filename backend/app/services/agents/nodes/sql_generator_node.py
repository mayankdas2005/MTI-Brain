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
from app.services.agents.helpers import merge_neo4j_raw_graph
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

    _pattern_nodes: list[dict] = []
    for _ap in (semantic_context.get("anti_patterns") or []):
        if _ap.get("id") or _ap.get("error_type"):
            _pattern_nodes.append({"_label": "AntiPattern", **_ap})
    for _qp in (semantic_context.get("query_patterns") or []):
        if _qp.get("id") or _qp.get("intent"):
            _pattern_nodes.append({"_label": "QueryPattern", **_qp})

    _neo4j_raw_graph = merge_neo4j_raw_graph(
        state.get("neo4j_raw_graph") or {},
        _pattern_nodes,
        [],
    ) if _pattern_nodes else None

    result: dict = {"sql_list": sql_list, "semantic_context": semantic_context}
    if _neo4j_raw_graph is not None:
        result["neo4j_raw_graph"] = _neo4j_raw_graph
    if first_sql:
        result["prior_sql"] = first_sql
    # Persist intra-turn caches so LangGraph keeps them across recompile invocations.
    if state.get("_sql_schema_ctx_cache") is not None:
        result["_sql_schema_ctx_cache"] = state["_sql_schema_ctx_cache"]
    if state.get("_cached_anti_patterns") is not None:
        result["_cached_anti_patterns"] = state["_cached_anti_patterns"]
    if state.get("_cached_query_patterns") is not None:
        result["_cached_query_patterns"] = state["_cached_query_patterns"]
    return result

"""Node 2: query_compiler — builds SemanticIR and generates SQL via LLM.

Single-query only (decomposition removed — a multi-CTE SQL handles all use cases).
Filter resolution happens BEFORE SQL compilation (routes to filter_resolver if needed).

Implementation is split across:
  ir_builder.py     — SemanticIR construction, join paths, column validation, filter specs
  schema_context.py — schema context for SQL LLM, join discovery, anti/query patterns
  sql_generator.py  — LLM call + spec logging
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.nodes.ir_builder import build_semantic_ir
from app.services.neo4j_analytics.nodes.sql_generator import generate_sql_llm
from app.services.neo4j_analytics.state import AnalyticsState


async def query_compiler(state: AnalyticsState, config: RunnableConfig) -> dict:
    resolved = state.get("resolved_intent") or {}
    semantic_context = state.get("semantic_context") or {}

    anchor_tables = resolved.get("anchor_tables") or []
    logger.info(
        "query_compiler START | thread={} | anchor_tables={} | complexity={}",
        state["thread_id"], anchor_tables, resolved.get("complexity"),
    )

    known_fqns = {t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")}
    missing = [t for t in anchor_tables if t not in known_fqns]
    if missing:
        logger.warning(
            "query_compiler | anchor_tables not in semantic_context — will attempt anyway | missing={} | thread={}",
            missing, state["thread_id"],
        )

    logger.info(
        "query_compiler | intent | thread={} | anchor_tables={} | measures={} | dimensions={} | filters={} | timeframe={}",
        state["thread_id"],
        anchor_tables,
        [(m.get("table_fqn", "").rsplit(".", 1)[-1] + "." + m.get("column_name", ""), m.get("aggregation")) for m in resolved.get("measures", [])],
        [d.get("column_name") for d in resolved.get("dimensions", [])],
        [(f.get("column_name") or f.get("column"), f.get("operator"), str(f.get("raw_value", ""))[:20]) for f in resolved.get("filters", [])],
        resolved.get("timeframe"),
    )

    return await _handle_single(state, resolved, semantic_context, config)


async def _handle_single(state: AnalyticsState, resolved: dict, semantic_context: dict, config: RunnableConfig) -> dict:
    try:
        ir = build_semantic_ir(resolved, semantic_context)
    except Exception as e:
        logger.error("query_compiler | IR build failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't map your question to the data model."}

    has_unresolved = any(not f.resolved for f in ir.filters)
    if ir.time_filter and not ir.time_filter.resolved:
        has_unresolved = True

    if has_unresolved:
        logger.info("query_compiler | unresolved filters | thread={} | routing to filter_resolver", state["thread_id"])
        return {
            "semantic_ir_list": [ir.model_dump()],
            "filter_resolution_needed": True,
            "sql_list": [],
        }

    try:
        sql = await generate_sql_llm(ir, semantic_context, state, config)
        if not sql:
            raise ValueError("LLM returned empty SQL")
        logger.info("query_compiler DONE | thread={} | sql_len={}", state["thread_id"], len(sql))
        return {
            "semantic_ir_list": [ir.model_dump()],
            "sql_list": [sql],
            "filter_resolution_needed": False,
            "prior_sql": sql,
        }
    except Exception as e:
        logger.exception("query_compiler | SQL generate failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't generate a valid query."}

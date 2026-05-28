"""Audit logging for the executor node.

Writes execution records to MTIBrainExecutionLog (DB) and QueryPattern/AntiPattern
nodes to Neo4j — all as fire-and-forget background tasks.
"""

from __future__ import annotations

import time
import uuid

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState


async def write_audit_log(state: AnalyticsState, sql: str, row_count: int, status: str) -> None:
    try:
        from app.db import async_session_factory
        from app.models.execution_log import MTIBrainExecutionLog

        ir_list = state.get("semantic_ir_list", [])
        anchor_tables = ir_list[0].get("anchor_tables", []) if ir_list else []
        schema_fqn = anchor_tables[0].rsplit(".", 1)[0] if anchor_tables else None
        elapsed_ms = round(
            (time.perf_counter() - state.get("pipeline_start_ms", time.perf_counter())) * 1000
        )

        log = MTIBrainExecutionLog(
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            user_email=state.get("user_email"),
            question=state["question"],
            question_type=state.get("question_type", "data_query"),
            schema_fqn=schema_fqn,
            tables_used=anchor_tables,
            sql=sql[:4000] if sql else "",
            row_count=row_count,
            fix_query_count=state.get("repair_count", 0),
            retry_count=state.get("recompile_count", 0),
            exec_error=state.get("execution_error") if status == "failed" else None,
            pattern_matched=state.get("pattern_matched", False),
            pattern_name=state.get("pattern_name"),
            duration_ms=elapsed_ms,
            max_rows=state.get("max_rows"),
            is_retry=state.get("is_retry", False),
            response_tone=state.get("persona", ""),
        )
        async with async_session_factory() as db:
            db.add(log)
            await db.commit()
    except Exception as e:
        logger.warning("audit | log write failed | error={}", e)


async def write_query_pattern(state: AnalyticsState, sql: str, ir: SemanticIR | None) -> None:
    if not ir:
        return
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        pattern_data = {
            "id": str(uuid.uuid4()),
            "question_text": state["question"],
            "sql_cte_outline": _extract_cte_outline(sql),
            "tables_used": ",".join(ir.anchor_tables),
            "intent": ir.intent,
            "complexity": ir.complexity,
            "user_id": state.get("user_id", ""),
            "cohere_embedding": embedding,
        }
        neo4j_client.write_query_pattern(pattern_data)
    except Exception as e:
        logger.warning("audit | write_query_pattern failed | error={}", e)


async def write_anti_pattern(state: AnalyticsState, sql: str, ir_dict: dict, error_msg: str) -> None:
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        pattern_data = {
            "id": str(uuid.uuid4()),
            "question_text": state["question"],
            "sql_fragment": sql[:500] if sql else "",
            "error_type": "execution_error",
            "error_summary": error_msg[:200],
            "tables_involved": ",".join(ir_dict.get("anchor_tables", [])),
            "intent": ir_dict.get("intent", ""),
            "cohere_embedding": embedding,
        }
        neo4j_client.write_anti_pattern(pattern_data)
    except Exception as e:
        logger.warning("audit | write_anti_pattern failed | error={}", e)


def _extract_cte_outline(sql: str) -> str:
    import re
    if not sql:
        return ""
    cte_names = re.findall(r"(\w+)\s+AS\s*\(", sql, re.IGNORECASE)
    table_refs = re.findall(r"FROM\s+(lpp\.\w+)", sql, re.IGNORECASE)
    return f"CTEs: {', '.join(cte_names[:4])} | Tables: {', '.join(set(table_refs))}"

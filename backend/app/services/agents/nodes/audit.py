"""Audit logging for the executor node.

Writes execution records to MTIBrainExecutionLog (DB) and QueryPattern/AntiPattern
nodes to Neo4j — all as fire-and-forget background tasks.
"""

from __future__ import annotations

import re
import time
import uuid

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState

_MIN_CONFIDENCE_FOR_PATTERN = 60


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


async def write_query_pattern(
    state: AnalyticsState,
    sql: str,
    ir: SemanticIR | None,
    confidence_score: int = 0,
    pattern_id: str | None = None,
) -> None:
    if not ir:
        return
    if confidence_score < _MIN_CONFIDENCE_FOR_PATTERN:
        logger.debug(
            "audit | QueryPattern skipped (low confidence) | score={} | threshold={}",
            confidence_score, _MIN_CONFIDENCE_FOR_PATTERN,
        )
        return
    recompile = state.get("recompile_count", 0)
    repair = state.get("repair_count", 0)
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        pattern_data = {
            "id": pattern_id or str(uuid.uuid4()),
            "question_text": state["question"],
            "sql_cte_outline": _extract_cte_outline(sql),
            "join_outline": _extract_join_outline(sql),
            "filter_summary": _extract_filter_summary(ir),
            "tables_used": list(ir.anchor_tables),
            "intent": ir.intent,
            "complexity": ir.complexity,
            "recompile_count": recompile,
            "repair_count": repair,
            "confidence_score": confidence_score,
            "row_count": state.get("query_summary", {}).get("total_rows") if state.get("query_summary") else None,
            "user_id": state.get("user_id", ""),
            "cohere_embedding": embedding,
            "promotion_status": "active",
            "liked_count": 0,
            "disliked_count": 0,
        }
        neo4j_client.write_query_pattern(pattern_data)
        logger.info(
            "audit | QueryPattern saved | id={} | confidence={} | intent={} | complexity={}",
            pattern_data["id"][:8], confidence_score, ir.intent, ir.complexity,
        )
    except Exception as e:
        logger.warning("audit | write_query_pattern failed | error={}", e)


async def write_schema_gaps(state: AnalyticsState) -> None:
    """Extract SCHEMA_GAP lines from intent_directive_context and upsert :SchemaGap nodes."""
    intent_context = (state.get("intent_directive_context") or "").strip()
    if not intent_context:
        return
    gaps = re.findall(r"SCHEMA_GAP:\s*(.+?)(?:\n|$)", intent_context, re.IGNORECASE)
    if not gaps:
        return
    ir_list = state.get("semantic_ir_list", [])
    tables = ir_list[0].get("anchor_tables", []) if ir_list else []
    thread_id = str(state.get("thread_id", ""))
    for raw in gaps:
        concept = raw.strip().rstrip(".,;")
        if concept:
            try:
                neo4j_client.write_schema_gap(concept, tables, thread_id)
            except Exception as e:
                logger.warning("audit | write_schema_gap failed | concept={} | error={}", concept, e)
    logger.debug("audit | {} schema gap(s) recorded | {}", len(gaps), [g.strip() for g in gaps[:3]])


async def write_anti_pattern(
    state: AnalyticsState,
    sql: str,
    ir_dict: dict,
    error_msg: str,
    error_type: str = "execution_error",
) -> None:
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        pattern_data = {
            "id": str(uuid.uuid4()),
            "question_text": state["question"],
            "sql_fragment": sql[:500] if sql else "",
            "error_type": error_type,
            "error_summary": error_msg[:300],
            "failing_element": _extract_failing_element(error_msg),
            "tables_involved": ",".join(ir_dict.get("anchor_tables", [])),
            "intent": ir_dict.get("intent", ""),
            "complexity": ir_dict.get("complexity", ""),
            "cohere_embedding": embedding,
        }
        neo4j_client.write_anti_pattern(pattern_data)
        logger.debug(
            "audit | AntiPattern saved | error_type={} | intent={} | element={}",
            error_type, ir_dict.get("intent"), pattern_data["failing_element"],
        )
    except Exception as e:
        logger.warning("audit | write_anti_pattern failed | error={}", e)


def _extract_cte_outline(sql: str) -> str:
    if not sql:
        return ""
    cte_names = re.findall(r"(\w+)\s+AS\s*\(", sql, re.IGNORECASE)
    table_refs = re.findall(r"FROM\s+(lpp\.\w+)", sql, re.IGNORECASE)
    return f"CTEs: {', '.join(cte_names[:4])} | Tables: {', '.join(set(table_refs))}"


def _extract_join_outline(sql: str) -> str:
    """Extract the JOIN clauses from the first CTE so future queries know the join keys."""
    if not sql:
        return ""
    joins = re.findall(
        r"(?:INNER\s+JOIN|LEFT\s+JOIN|JOIN)\s+(lpp\.\w+)\s+ON\s+([^\n]+)",
        sql, re.IGNORECASE,
    )
    if not joins:
        return ""
    return " | ".join(f"JOIN {tbl} ON {clause.strip()[:80]}" for tbl, clause in joins[:4])


def _extract_filter_summary(ir: SemanticIR) -> str:
    """Summarise the filter types used so future queries know what patterns worked."""
    if not ir.filters and not ir.time_filter:
        return "no filters"
    parts = []
    if ir.time_filter:
        parts.append(f"date: {ir.time_filter.column_name} {ir.time_filter.operator}")
    ops = {}
    for f in ir.filters:
        ops.setdefault(f.operator, []).append(f.column_name)
    for op, cols in ops.items():
        parts.append(f"{op} on {', '.join(cols[:2])}")
    return " | ".join(parts)


def _extract_failing_element(error_msg: str) -> str:
    """Pull the most specific identifier from an error message (column/table name)."""
    m = re.search(r'(?:column|relation|table)\s+"([^"]+)"', error_msg, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'(?:undefined|unknown|invalid)\s+(\S+)', error_msg, re.IGNORECASE)
    if m:
        return m.group(1).rstrip(".,;")
    return ""

"""LLM-based SQL repair for the executor node.

Called when executor gets a DB error and repair_count < MAX_REPAIR.
Uses Opus to rewrite the broken SQL from error + schema context.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP, REPAIR_PROMPT
from app.services.neo4j_analytics.sql_validator_logic import validate_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def attempt_repair(
    state: AnalyticsState,
    sql_list: list[str],
    ir_list: list[dict],
    errors: list[str],
    repair_count: int,
    config: RunnableConfig,
    schema_context: dict | None = None,
) -> dict | None:
    """Use Opus to repair broken SQL. Returns updated state dict or None."""
    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    logger.warning("repair | attempting repair | thread={} | repair_count={}", state["thread_id"], repair_count)

    anti_patterns = "(none)"
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if patterns:
            ap_lines = []
            for p in patterns:
                element = p.get("failing_element")
                line = f"- [{p.get('error_type', 'error')}]"
                if element:
                    line += f" element={element} |"
                line += f" {p.get('error_summary', '')}"
                ap_lines.append(line)
            anti_patterns = "\n".join(ap_lines)
    except Exception:
        pass

    sc = schema_context or {}
    first_sql = sql_list[0] if sql_list else ""
    first_ir = ir_list[0] if ir_list else {}
    error_msg = "; ".join(errors[:3])

    semantic_ir_text = _build_semantic_ir_text(first_ir)
    schema_reference = _build_schema_reference_for_repair(sc)

    prior_attempts_detail = ""
    if repair_count > 0 and state.get("execution_error"):
        prior_attempts_detail = (
            f"PRIOR REPAIR ATTEMPTS:\n"
            f"This is repair attempt {repair_count + 1}. "
            f"The PREVIOUS repair attempt produced this NEW error: {state['execution_error']}\n"
            "Do NOT try the same fix again — use a completely different approach."
        )
    else:
        prior_attempts_detail = f"PRIOR REPAIR ATTEMPTS:\nThis is repair attempt {repair_count + 1}."

    fb = state.get("feedback_context") or ""
    feedback_section = (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n<feedback_context>{fb}</feedback_context>"
        if fb else ""
    )

    prompt = REPAIR_PROMPT.format_messages(
        semantic_ir_text=semantic_ir_text,
        schema_reference=schema_reference,
        original_sql=first_sql,
        error_message=error_msg,
        prior_attempts_detail=prior_attempts_detail,
        feedback_section=feedback_section,
        anti_patterns=anti_patterns,
        reasoning_directive=REASONING_DIRECTIVE_DEEP,
    )

    llm = get_llm("deep")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    try:
        response = await _call()
    except Exception as e:
        logger.error("repair | LLM failed | thread={} | error={}", state["thread_id"], e)
        return None

    raw = response.content or ""
    repaired_sql = parse_tag(raw, "sql")
    if not repaired_sql:
        logger.warning("repair | produced no SQL | thread={}", state["thread_id"])
        return None

    is_valid, val_error = validate_sql(repaired_sql)
    if not is_valid:
        logger.warning("repair | repaired SQL failed validation | thread={} | error={}", state["thread_id"], val_error)
        from app.services.neo4j_analytics.nodes.audit import write_anti_pattern
        asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="validation_error"))
        return None

    new_sql_list = [repaired_sql] + sql_list[1:]
    logger.info("repair | succeeded | thread={}", state["thread_id"])
    from app.services.neo4j_analytics.nodes.audit import write_anti_pattern, write_audit_log
    asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="repair_input"))
    asyncio.create_task(write_audit_log(state, first_sql, 0, "repaired"))
    return {
        "sql_list": new_sql_list,
        "repair_count": repair_count + 1,
        "error": None,
    }


def _build_semantic_ir_text(ir_dict: dict) -> str:
    """Build structured QUERY INTENT text replacing json.dumps(first_ir)."""
    if not ir_dict:
        return "--- QUERY INTENT ---\n\n(no intent available)"

    lines = ["--- QUERY INTENT (preserve this — do not change the semantic meaning) ---", ""]

    intent = ir_dict.get("intent", "")
    complexity = ir_dict.get("complexity", "")
    anchor_tables = ir_dict.get("anchor_tables", [])
    measures = ir_dict.get("measures", [])
    dimensions = ir_dict.get("dimensions", [])
    time_filter = ir_dict.get("time_filter")
    filters = ir_dict.get("filters", [])
    join_clauses = ir_dict.get("join_clauses", [])
    path_tables = ir_dict.get("path_tables", [])

    if intent:
        lines.append(f"Intent:     {intent}")
    if complexity:
        lines.append(f"Complexity: {complexity}")
    if anchor_tables:
        lines.append(f"Tables:     {', '.join(anchor_tables)}")

    if measures:
        measure_strs = []
        for m in measures:
            agg = m.get("aggregation") or "SUM"
            fqn = m.get("table_fqn", "")
            col = m.get("column_name", "")
            alias = m.get("alias", col)
            measure_strs.append(f"{agg}({fqn}.{col}) AS {alias}")
        lines.append(f"Measures:   {', '.join(measure_strs)}")

    if dimensions:
        dim_strs = [f"{d.get('table_fqn', '')}.{d.get('column_name', '')}" for d in dimensions]
        lines.append(f"Dimensions: {', '.join(dim_strs)}")

    if time_filter:
        tf_col = f"{time_filter.get('table_fqn', '')}.{time_filter.get('column_name', time_filter.get('column', ''))}"
        tf_val = time_filter.get("value", "")
        lines.append(f"Time:       {tf_col}  {tf_val}")

    if filters:
        filter_strs = []
        for f in filters:
            col = f"{f.get('table_fqn', '')}.{f.get('column_name', '')}"
            op = f.get("operator", "=")
            val = f.get("value", "")
            filter_strs.append(f"{col} {op} '{val}'")
        lines.append(f"Filters:    {', '.join(filter_strs)}")

    valid_joins = [c for c in join_clauses if c]
    if valid_joins:
        lines.append(f"Joins:      {', '.join(valid_joins)}")

    return "\n".join(lines)


def _build_schema_reference_for_repair(sc: dict) -> str:
    """Build structured SCHEMA REFERENCE for repair (minimal — tables + column types only)."""
    tables = sc.get("tables", [])
    columns = sc.get("columns", [])

    lines = ["--- SCHEMA REFERENCE ---", ""]

    if tables:
        lines.append("TABLES:")
        for t in tables[:8]:
            fqn = t.get("fqn", "")
            desc = t.get("description", "")
            desc_str = f" — {desc}" if desc else ""
            lines.append(f"  {fqn}{desc_str}")
        lines.append("")

    if columns:
        lines.append("PRIMARY COLUMNS (use these to fix column names and types):")
        for c in columns[:25]:
            table_fqn = c.get("table_fqn", "")
            name = c.get("name", "")
            dtype = c.get("data_type", "")
            lines.append(f"  {table_fqn}.{name:<45} {dtype}")

    return "\n".join(lines)

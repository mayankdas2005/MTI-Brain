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
    import json
    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    logger.warning("repair | attempting repair | thread={} | repair_count={}", state["thread_id"], repair_count)

    anti_patterns = "(none)"
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if patterns:
            anti_patterns = "\n".join(f"- {p.get('error_type', '')}: {p.get('error_summary', '')}" for p in patterns)
    except Exception:
        pass

    sc = schema_context or {}
    schema_summary = json.dumps({
        "tables": [
            {"fqn": t.get("fqn"), "description": t.get("description", "")}
            for t in sc.get("tables", [])[:8]
        ],
        "columns": [
            {"table_fqn": c.get("table_fqn"), "name": c.get("name"), "data_type": c.get("data_type", "")}
            for c in sc.get("columns", [])[:25]
        ],
    }, indent=2)

    first_sql = sql_list[0] if sql_list else ""
    first_ir = ir_list[0] if ir_list else {}
    error_msg = "; ".join(errors[:3])

    prior_attempts_detail = f"This is repair attempt {repair_count + 1}."
    if repair_count > 0 and state.get("execution_error"):
        prior_attempts_detail += (
            f"\nThe PREVIOUS repair attempt produced this NEW error: {state['execution_error']}"
            "\nDo NOT try the same fix again — use a completely different approach."
        )

    fb = state.get("feedback_context") or ""
    feedback_section = (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n<feedback_context>{fb}</feedback_context>"
        if fb else ""
    )

    prompt = REPAIR_PROMPT.format_messages(
        semantic_ir=json.dumps(first_ir, indent=2),
        schema_context=schema_summary,
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
        asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg))
        return None

    new_sql_list = [repaired_sql] + sql_list[1:]
    logger.info("repair | succeeded | thread={}", state["thread_id"])
    from app.services.neo4j_analytics.nodes.audit import write_anti_pattern, write_audit_log
    asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg))
    asyncio.create_task(write_audit_log(state, first_sql, 0, "repaired"))
    return {
        "sql_list": new_sql_list,
        "repair_count": repair_count + 1,
        "error": None,
    }

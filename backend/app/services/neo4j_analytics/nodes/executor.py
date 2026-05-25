"""Node 3: executor — runs SQL on Redshift, handles zero-row probing, repair, and audit logging.

Repair uses Opus (max 2 attempts). Writes to MTIBrainExecutionLog for all attempts.
QueryPattern/AntiPattern nodes written as async background tasks.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.filter_resolver_logic import is_time_sensitive_sql
from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP, REPAIR_PROMPT
from app.services.neo4j_analytics.result_summarizer import summarize_results
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.sql_validator_logic import validate_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def executor(state: AnalyticsState, config: dict) -> dict:
    sql_list = state.get("sql_list", [])
    ir_list = state.get("semantic_ir_list", [])
    repair_count = state.get("repair_count", 0)
    logger.info("executor START | thread={} | sql_count={} | repair_count={}", state["thread_id"], len(sql_list), repair_count)

    if not sql_list or not ir_list:
        logger.warning("executor | no SQL to execute | thread={}", state["thread_id"])
        return {"error": "No SQL available to execute.", "no_data": True}

    complexity = ir_list[0].get("complexity", "simple") if ir_list else "simple"
    timeout_map = {"simple": 15, "complex": 30, "advanced": 60}
    query_timeout = timeout_map.get(complexity, 30)

    result_list = []
    all_columns = []
    all_rows = []
    reliability_flags = list(state.get("reliability_flags") or [])

    independent_indices = [i for i, ir in enumerate(ir_list) if not ir.get("depends_on")]
    dependent_indices = [i for i, ir in enumerate(ir_list) if ir.get("depends_on") is not None]

    parallel_results = await asyncio.gather(
        *[_execute_single(sql_list[i], ir_list[i], state, query_timeout) for i in independent_indices],
        return_exceptions=True,
    )

    for i, idx in enumerate(independent_indices):
        res = parallel_results[i]
        if isinstance(res, Exception):
            logger.error("executor | sub-query {} failed | thread={} | error={}", idx, state["thread_id"], res)
            result_list.append({"index": idx, "error": str(res), "columns": [], "rows": []})
        else:
            result_list.append({"index": idx, **res})
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            if res.get("rows"):
                all_rows.extend(res["rows"])

    for idx in dependent_indices:
        dep_idx = ir_list[idx].get("depends_on")
        dep_result = next((r for r in result_list if r["index"] == dep_idx), None)
        if dep_result and dep_result.get("error"):
            result_list.append({"index": idx, "error": "dependency failed", "columns": [], "rows": []})
            continue
        try:
            res = await _execute_single(sql_list[idx], ir_list[idx], state, query_timeout)
            result_list.append({"index": idx, **res})
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            if res.get("rows"):
                all_rows.extend(res["rows"])
        except Exception as e:
            result_list.append({"index": idx, "error": str(e), "columns": [], "rows": []})

    all_errors = [r["error"] for r in result_list if r.get("error")]
    first_ir = SemanticIR(**ir_list[0]) if ir_list else None

    if all_errors and repair_count < 2:
        repair_result = await _attempt_repair(state, sql_list, ir_list, all_errors, repair_count, config)
        if repair_result:
            return repair_result

    total_rows = len(all_rows)

    if total_rows == 0 and not all_errors:
        probe_result = await _zero_row_probe(first_ir, state)
        if probe_result.get("needs_clarification"):
            return {
                "result_list": result_list,
                "no_data": True,
                "zero_row_probe_result": probe_result.get("reason"),
                "needs_clarification": True,
                "clarification_reason": probe_result.get("reason"),
            }
        return {
            "result_list": result_list,
            "no_data": True,
            "zero_row_probe_result": probe_result.get("reason"),
        }

    reliability_flags = _check_reliability(first_ir, all_rows, reliability_flags)

    query_summary = summarize_results(
        columns=all_columns,
        rows=all_rows,
        intent=first_ir.intent if first_ir else "",
        reliability_flags=reliability_flags,
    )

    asyncio.create_task(_write_audit_log(state, sql_list[0] if sql_list else "", total_rows, "success"))
    if not all_errors and total_rows > 0:
        asyncio.create_task(_write_query_pattern(state, sql_list[0] if sql_list else "", first_ir))

    logger.info("executor DONE | thread={} | rows={} | flags={}", state["thread_id"], total_rows, reliability_flags)
    return {
        "result_list": result_list,
        "query_summary": query_summary.model_dump(),
        "no_data": False,
        "reliability_flags": reliability_flags,
        "error": None,
    }


async def _execute_single(sql: str, ir_dict: dict, state: AnalyticsState, timeout_s: int) -> dict:
    """Execute a single SQL query, checking Redis cache first."""
    from app.services.neo4j_analytics.redshift_client import execute_query

    if not is_time_sensitive_sql(sql):
        cached = redis_client.get_redshift_result(sql)
        if cached:
            columns, rows = cached
            logger.debug("executor | cache hit | thread={} | rows={}", state["thread_id"], len(rows))
            return {"columns": columns, "rows": rows, "cached": True}

    columns, rows = await execute_query(sql, timeout_s=timeout_s, thread_id=state["thread_id"])

    if not is_time_sensitive_sql(sql) and rows and 0 < len(rows) <= 5000:
        redis_client.set_redshift_result(sql, columns, rows, ttl=14400)

    return {"columns": columns, "rows": rows, "cached": False}


async def _zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    """Z1-Z3 zero row probe logic."""
    if not ir or not ir.anchor_tables:
        return {"needs_clarification": False, "reason": "No data found for the requested query."}

    from app.services.neo4j_analytics.redshift_client import execute_query
    anchor = ir.anchor_tables[0]
    time_filter = ir.time_filter

    if time_filter and time_filter.resolved:
        if isinstance(time_filter.value, list) and len(time_filter.value) == 2:
            time_where = f"{time_filter.table_fqn}.{time_filter.column_name} BETWEEN '{time_filter.value[0]}' AND '{time_filter.value[1]}'"
        else:
            time_where = f"{time_filter.table_fqn}.{time_filter.column_name} = '{time_filter.value}'"
        probe_sql = f"SELECT COUNT(*) AS cnt FROM {anchor} WHERE {time_where}"
        try:
            _, rows = await execute_query(probe_sql, timeout_s=10, thread_id=state["thread_id"])
            count = int(rows[0][0]) if rows and rows[0] else 0
            if count == 0:
                return {"needs_clarification": False, "reason": f"No data exists in `{anchor}` for the requested time period."}
            return {"needs_clarification": True, "reason": f"Data exists in `{anchor}` for that period ({count} records) but none match all the filters. Try broadening your criteria."}
        except Exception as e:
            logger.warning("executor | zero row probe failed | error={}", e)

    return {"needs_clarification": False, "reason": "No data found matching the query criteria."}


async def _attempt_repair(
    state: AnalyticsState,
    sql_list: list[str],
    ir_list: list[dict],
    errors: list[str],
    repair_count: int,
    config: dict,
) -> dict | None:
    """Use Opus to repair broken SQL. Returns updated state dict or None."""
    import json
    from app.services.agents.bedrock import get_llm
    from app.core.langfuse_integration import create_callback_handler, langfuse_context

    logger.warning("executor | attempting repair | thread={} | repair_count={}", state["thread_id"], repair_count)

    anti_patterns = "(none)"
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if patterns:
            anti_patterns = "\n".join(f"- {p.get('error_type', '')}: {p.get('error_summary', '')}" for p in patterns)
    except Exception:
        pass

    first_sql = sql_list[0] if sql_list else ""
    first_ir = ir_list[0] if ir_list else {}
    error_msg = "; ".join(errors[:3])

    prompt = REPAIR_PROMPT.format_messages(
        semantic_ir=json.dumps(first_ir, indent=2),
        original_sql=first_sql,
        error_message=error_msg,
        prior_attempts=f"Attempt {repair_count}" if repair_count > 0 else "First attempt",
        anti_patterns=anti_patterns,
        reasoning_directive=REASONING_DIRECTIVE_DEEP,
    )

    llm = get_llm("deep")
    handler = create_callback_handler()
    callbacks = [handler] if handler else []

    try:
        with langfuse_context(session_id=state["thread_id"], user_id=state["user_id"], tags=["neo4j_analytics", "repair"]):
            response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    except Exception as e:
        logger.error("executor | repair LLM failed | thread={} | error={}", state["thread_id"], e)
        return None

    raw = response.content or ""
    repaired_sql = parse_tag(raw, "sql")
    if not repaired_sql:
        logger.warning("executor | repair produced no SQL | thread={}", state["thread_id"])
        return None

    is_valid, val_error = validate_sql(repaired_sql)
    if not is_valid:
        logger.warning("executor | repaired SQL failed validation | thread={} | error={}", state["thread_id"], val_error)
        asyncio.create_task(_write_anti_pattern(state, first_sql, first_ir, error_msg))
        return None

    new_sql_list = [repaired_sql] + sql_list[1:]
    logger.info("executor | repair succeeded | thread={}", state["thread_id"])
    asyncio.create_task(_write_anti_pattern(state, first_sql, first_ir, error_msg))
    asyncio.create_task(_write_audit_log(state, first_sql, 0, "repaired"))
    return {
        "sql_list": new_sql_list,
        "repair_count": repair_count + 1,
        "error": None,
    }


def _check_reliability(ir: SemanticIR | None, rows: list, flags: list[str]) -> list[str]:
    if not ir or not rows:
        return flags

    intent = (ir.intent or "").lower()

    if "kpi" in intent or "total" in intent:
        if len(rows) > 50:
            if "unexpected_row_count" not in flags:
                flags.append("unexpected_row_count")

    if "trend" in intent or "over time" in intent:
        if len(rows) == 1:
            if "trend_insufficient_data" not in flags:
                flags.append("trend_insufficient_data")

    return flags


async def _write_audit_log(state: AnalyticsState, sql: str, row_count: int, status: str) -> None:
    try:
        from app.db import async_session_factory
        from app.models.execution_log import MTIBrainExecutionLog

        ir_list = state.get("semantic_ir_list", [])
        anchor_tables = ir_list[0].get("anchor_tables", []) if ir_list else []

        log = MTIBrainExecutionLog(
            user_id=state.get("user_id"),
            question=state["question"],
            question_type="data_query",
            sql=sql[:4000] if sql else "",
            tables_used=",".join(anchor_tables),
            row_count=row_count,
            fix_query_count=state.get("repair_count", 0),
            retry_count=state.get("recompile_count", 0),
            response_tone=state.get("persona", ""),
        )
        async with async_session_factory() as db:
            db.add(log)
            await db.commit()
    except Exception as e:
        logger.warning("executor | audit log write failed | error={}", e)


async def _write_query_pattern(state: AnalyticsState, sql: str, ir: SemanticIR | None) -> None:
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
        logger.warning("executor | write_query_pattern failed | error={}", e)


async def _write_anti_pattern(state: AnalyticsState, sql: str, ir_dict: dict, error_msg: str) -> None:
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
        logger.warning("executor | write_anti_pattern failed | error={}", e)


def _extract_cte_outline(sql: str) -> str:
    """Extract just the CTE names and tables from SQL for pattern storage."""
    import re
    if not sql:
        return ""
    cte_names = re.findall(r"(\w+)\s+AS\s*\(", sql, re.IGNORECASE)
    table_refs = re.findall(r"FROM\s+(lpp\.\w+)", sql, re.IGNORECASE)
    return f"CTEs: {', '.join(cte_names[:4])} | Tables: {', '.join(set(table_refs))}"

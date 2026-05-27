"""Node 3: executor — runs SQL on Redshift, handles zero-row probing, repair, and audit logging.

Repair uses Opus (max 2 attempts). Writes to MTIBrainExecutionLog for all attempts.
QueryPattern/AntiPattern nodes written as async background tasks.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import asyncio
import time
import uuid

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP, REPAIR_PROMPT
from app.services.neo4j_analytics.result_summarizer import summarize_results
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.sql_validator_logic import validate_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def executor(state: AnalyticsState, config: RunnableConfig) -> dict:
    sql_list = state.get("sql_list", [])
    ir_list = state.get("semantic_ir_list", [])
    repair_count = state.get("repair_count", 0)
    logger.info("executor START | thread={} | sql_count={} | repair_count={}", state["thread_id"], len(sql_list), repair_count)

    if not sql_list or not ir_list:
        logger.warning("executor | no SQL to execute | thread={}", state["thread_id"])
        return {"error": "No SQL available to execute.", "no_data": True}

    query_timeout = 300

    result_list = []
    all_columns = []
    all_rows = []
    reliability_flags = list(state.get("reliability_flags") or [])
    max_rows = state.get("max_rows", 100)

    merged_sql = _merge_sql_list(sql_list, ir_list)
    if merged_sql:
        logger.info("executor | running merged SQL | thread={} | sql_preview={}", state["thread_id"], merged_sql)
        try:
            res = await _execute_single(merged_sql, ir_list[0], state, query_timeout, max_rows)
            result_list = [{"index": 0, **res}]
            all_columns = res.get("columns", [])
            all_rows = res.get("rows", [])
            logger.info("executor | merged SQL result | thread={} | rows={} | columns={}", state["thread_id"], len(all_rows), all_columns)
        except Exception as e:
            logger.warning("executor | merged SQL failed, fallback | thread={} | error={}", state["thread_id"], e)
            merged_sql = None

    if not merged_sql:
        independent_indices = [i for i, ir in enumerate(ir_list) if ir.get("depends_on") is None]
        dependent_indices = [i for i, ir in enumerate(ir_list) if ir.get("depends_on") is not None]

        for i in independent_indices:
            logger.info("executor | running sub-query {} | thread={} | sql_preview={}", i, state["thread_id"], sql_list[i])

        parallel_results = await asyncio.gather(
            *[_execute_single(sql_list[i], ir_list[i], state, query_timeout, max_rows) for i in independent_indices],
            return_exceptions=True,
        )

        for i, idx in enumerate(independent_indices):
            res = parallel_results[i]
            if isinstance(res, Exception):
                logger.error("executor | sub-query {} FAILED | thread={} | error={}", idx, state["thread_id"], res)
                result_list.append({"index": idx, "error": str(res), "columns": [], "rows": []})
            else:
                logger.info("executor | sub-query {} result | thread={} | rows={} | columns={}", idx, state["thread_id"], len(res.get("rows", [])), res.get("columns", []))
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
            logger.info("executor | running dependent sub-query {} | thread={} | sql_preview={}", idx, state["thread_id"], sql_list[idx][:400])
            try:
                res = await _execute_single(sql_list[idx], ir_list[idx], state, query_timeout, max_rows)
                logger.info("executor | dependent sub-query {} result | thread={} | rows={} | columns={}", idx, state["thread_id"], len(res.get("rows", [])), res.get("columns", []))
                result_list.append({"index": idx, **res})
                if not all_columns and res.get("columns"):
                    all_columns = res["columns"]
                if res.get("rows"):
                    all_rows.extend(res["rows"])
            except Exception as e:
                logger.error("executor | dependent sub-query {} FAILED | thread={} | error={}", idx, state["thread_id"], e)
                result_list.append({"index": idx, "error": str(e), "columns": [], "rows": []})

    all_errors = [r["error"] for r in result_list if r.get("error")]
    first_ir = SemanticIR(**ir_list[0]) if ir_list else None

    if all_errors and repair_count < 2:
        repair_result = await _attempt_repair(
            state, sql_list, ir_list, all_errors, repair_count, config,
            schema_context=state.get("semantic_context") or {},
        )
        if repair_result:
            return repair_result

    if all_errors:
        partial_rows = []
        partial_cols: list = []
        for r in result_list:
            if r.get("rows"):
                partial_rows.extend(r["rows"])
            if not partial_cols and r.get("columns"):
                partial_cols = r["columns"]

        if partial_rows:
            logger.warning(
                "executor | repairs exhausted but partial data available | thread={} | rows={} | failed_errors={}",
                state["thread_id"], len(partial_rows),
                [r.get("error") for r in result_list if r.get("error")],
            )
            all_rows = partial_rows
            all_columns = partial_cols
            all_errors = []
        else:
            combined_error = "; ".join(all_errors[:3])
            logger.warning("executor | repairs exhausted, no usable data | thread={} | error={}", state["thread_id"], combined_error)
            asyncio.create_task(_write_audit_log(state, sql_list[0] if sql_list else "", 0, "failed"))
            return {
                "result_list": result_list,
                "error": combined_error,
                "execution_error": combined_error,
                "no_data": True,
                "repair_count": repair_count,
                "_prev_repair_count": repair_count,
            }

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
                "_prev_repair_count": repair_count,
            }
        return {
            "result_list": result_list,
            "no_data": True,
            "zero_row_probe_result": probe_result.get("reason"),
            "_prev_repair_count": repair_count,
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

    logger.info(
        "executor DONE | thread={} | total_rows={} | columns={} | no_data=False | flags={} | passing to synthesis",
        state["thread_id"], total_rows, all_columns, reliability_flags,
    )
    return {
        "result_list": result_list,
        "query_summary": query_summary.model_dump(),
        "no_data": False,
        "reliability_flags": reliability_flags,
        "error": None,
        "execution_error": None,
        "_prev_repair_count": repair_count,
        "zero_row_probe_result": None,
        "needs_clarification": False,
        "clarification_reason": None,
    }


async def _execute_single(sql: str, ir_dict: dict, state: AnalyticsState, timeout_s: int, max_rows: int = 100) -> dict:
    from app.services.neo4j_analytics.redshift_client import execute_query

    bounded_sql = _apply_row_limit(sql, max_rows)
    columns, rows = await execute_query(bounded_sql, timeout_s=timeout_s, thread_id=state["thread_id"])
    rows = _make_rows_json_safe(rows[:max_rows])
    return {"columns": columns, "rows": rows, "cached": False}


def _apply_row_limit(sql: str, max_rows: int) -> str:
    """Inject or tighten the LIMIT clause in the SQL.

    Only touches the outermost LIMIT — never modifies subqueries.
    Uses a simple regex on the final SELECT block (safe for CTE-style queries
    where the outer SELECT * FROM final has the only top-level LIMIT).
    """
    import re
    limit_pattern = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)
    match = limit_pattern.search(sql.rstrip())
    if match:
        existing = int(match.group(1))
        if existing <= max_rows:
            return sql
        # Replace existing LIMIT with the smaller cap
        return limit_pattern.sub(f"LIMIT {max_rows}", sql.rstrip())
    return sql.rstrip() + f"\nLIMIT {max_rows}"


def _merge_sql_list(sql_list: list[str], ir_list: list[dict]) -> str | None:
    """Combine multiple sub-query SQLs into one when merge_strategy is set.

    Returns a single SQL string or None (fall back to sequential execution).
    Redshift supports CTEs inside subquery expressions, so wrapping each
    WITH…SELECT in (...) AS alias is valid.
    """
    if len(sql_list) <= 1:
        return None
    strategy = next((ir.get("merge_strategy") for ir in ir_list if ir.get("merge_strategy")), None)
    if not strategy:
        return None
    if strategy == "join":
        merge_key = next((ir.get("merge_key") for ir in ir_list if ir.get("merge_key")), None)
        if merge_key and len(sql_list) == 2:
            join_conds = " AND ".join(f"_sq0.{k} = _sq1.{k}" for k in merge_key)
            return (
                f"SELECT * FROM ({sql_list[0]}) AS _sq0\n"
                f"JOIN ({sql_list[1]}) AS _sq1 ON {join_conds}"
            )
    # union / labeled_sets / fallback
    parts = [f"({sql})" for sql in sql_list]
    return "\nUNION ALL\n".join(parts)


def _make_rows_json_safe(rows: list[list]) -> list[list]:
    """Convert datetime.date and Decimal values to JSON-serializable types."""
    import datetime
    import decimal
    safe = []
    for row in rows:
        safe_row = []
        for v in row:
            if isinstance(v, datetime.datetime):
                safe_row.append(v.isoformat())
            elif isinstance(v, datetime.date):
                safe_row.append(v.isoformat())
            elif isinstance(v, decimal.Decimal):
                safe_row.append(float(v))
            else:
                safe_row.append(v)
        safe.append(safe_row)
    return safe


def _quote_scalar(v: str) -> str:
    """No quotes for numerics, single-quoted for strings."""
    try:
        float(v)
        return str(v)
    except (ValueError, TypeError):
        return f"'{v}'"


def _build_filter_condition(col_ref: str, f) -> str | None:
    """Build a SQL predicate from a FilterSpec with correct value quoting."""
    op = f.operator.upper()
    val = f.value
    if op in ("BETWEEN", "BETWEEN_SQL") and isinstance(val, list) and len(val) == 2:
        v0 = str(val[0]) if f.is_raw_sql else _quote_scalar(val[0])
        v1 = str(val[1]) if f.is_raw_sql else _quote_scalar(val[1])
        return f"{col_ref} BETWEEN {v0} AND {v1}"
    if op == "IN" and isinstance(val, list):
        formatted = ", ".join(str(v) if f.is_raw_sql else _quote_scalar(v) for v in val)
        return f"{col_ref} IN ({formatted})"
    if isinstance(val, str):
        formatted = val if f.is_raw_sql else _quote_scalar(val)
        return f"{col_ref} {f.operator} {formatted}"
    return None


def _rewrite_table_aliases(text: str, aliases: dict[str, str]) -> str:
    """Replace fully-qualified table names with t0/t1 aliases."""
    result = text
    for fqn, alias in sorted(aliases.items(), key=lambda x: -len(x[0])):
        result = result.replace(f"{fqn}.", f"{alias}.")
    return result


def _build_probe_from_clause(ir: SemanticIR) -> tuple[str, dict[str, str]] | None:
    """Build FROM + JOIN string and alias map from a SemanticIR.

    Uses path_tables (not anchor_tables) so intermediate join tables are
    included and join_clauses align 1-to-1 with consecutive table pairs.
    """
    tables = ir.path_tables if ir.path_tables else ir.anchor_tables
    if not tables:
        return None
    aliases = {t: f"t{i}" for i, t in enumerate(tables)}
    parts = [f"{tables[0]} AS t0"]
    for i in range(1, len(tables)):
        if i - 1 >= len(ir.join_clauses) or i - 1 >= len(ir.join_types):
            break
        raw_jt = (ir.join_types[i - 1] or "INNER JOIN").upper().strip()
        join_kw = raw_jt if "JOIN" in raw_jt else f"{raw_jt} JOIN"
        rewritten = _rewrite_table_aliases(ir.join_clauses[i - 1], aliases)
        parts.append(f"{join_kw} {tables[i]} AS t{i} ON {rewritten}")
    return "\n".join(parts), aliases


def _describe_filters(filters: list) -> str:
    parts = []
    for f in filters:
        val_str = ", ".join(str(v) for v in f.value) if isinstance(f.value, list) else str(f.value)
        parts.append(f"`{f.column_name} {f.operator} {val_str}`")
    return " and ".join(parts)


async def _zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    """Three-stage zero-row diagnosis.

    Stage 1 — time-only: SELECT COUNT(*) FROM time_table WHERE time_col op value
      Uses the time filter's own table and unqualified column name to avoid
      3-part schema.table.column references that some Redshift configs reject.
      → 0: "No data for this period" (needs_clarification=False)
      → N: proceed to Stage 2

    Stage 2 — time + JOINs + WHERE filters:
      Builds FROM using path_tables (not anchor_tables) so intermediate join
      tables are included and join_clauses align correctly.
      → 0: "WHERE/JOIN conditions too restrictive" (needs_clarification=True)
      → N (or stage 2 fails): proceed to Stage 3

    Stage 3 — HAVING by elimination (no extra DB call):
      If count2 > 0 and having_filters exist, HAVING must be the culprit.
      → "Aggregate threshold too strict" (needs_clarification=True)
    """
    if not ir or not ir.anchor_tables:
        return {"needs_clarification": False, "reason": "No data found for the requested query."}

    from app.services.neo4j_analytics.redshift_client import execute_query
    time_filter = ir.time_filter

    if not time_filter or not time_filter.resolved:
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    # Use unqualified column_name with FROM time_filter.table_fqn.
    # Avoids 3-part schema.table.column in WHERE which is fragile on Redshift.
    time_table = time_filter.table_fqn
    s1_cond = _build_filter_condition(time_filter.column_name, time_filter)
    if not s1_cond:
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    try:
        _, rows = await execute_query(
            f"SELECT COUNT(*) AS cnt FROM {time_table} WHERE {s1_cond}",
            timeout_s=10, thread_id=state["thread_id"],
        )
        count1 = int(rows[0][0]) if rows and rows[0] else 0
    except Exception as e:
        logger.warning("executor | zero row probe stage 1 failed | error={}", e)
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    if count1 == 0:
        return {
            "needs_clarification": False,
            "reason": f"No data exists in `{time_table}` for the requested time period.",
        }

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    where_filters = [f for f in (ir.filters or []) if not f.is_having and f.resolved]
    having_filters = [f for f in (ir.filters or []) if f.is_having and f.resolved]
    count2 = count1  # default: treat stage 2 as passing if it errors

    from_info = _build_probe_from_clause(ir)
    if from_info:
        from_clause, aliases = from_info

        # Build time condition using the alias for time_filter.table_fqn.
        # Fall back to unqualified column_name if the table isn't in the alias map
        # (shouldn't happen, but safe to guard).
        t_alias = aliases.get(time_filter.table_fqn)
        t_col_ref = f"{t_alias}.{time_filter.column_name}" if t_alias else time_filter.column_name
        s2_time_cond = _build_filter_condition(t_col_ref, time_filter)

        where_parts = [s2_time_cond] if s2_time_cond else []
        for f in where_filters:
            f_alias = aliases.get(f.table_fqn)
            # If alias not found, use plain column_name (avoids 3-part ref in aliased context)
            col_ref = f"{f_alias}.{f.column_name}" if f_alias else f.column_name
            cond = _build_filter_condition(col_ref, f)
            if cond:
                where_parts.append(cond)

        if where_parts:
            stage2_sql = f"SELECT COUNT(*) AS cnt\nFROM {from_clause}\nWHERE {' AND '.join(where_parts)}"
            try:
                _, rows = await execute_query(stage2_sql, timeout_s=10, thread_id=state["thread_id"])
                count2 = int(rows[0][0]) if rows and rows[0] else 0
            except Exception as e:
                logger.warning("executor | zero row probe stage 2 failed | error={}", e)

    if count2 == 0:
        if where_filters:
            desc = _describe_filters(where_filters)
            return {
                "needs_clarification": True,
                "reason": (
                    f"Data exists for this period ({count1:,} records) but the WHERE filter(s) "
                    f"{desc} return no results. Try broadening these filter values."
                ),
            }
        return {
            "needs_clarification": True,
            "reason": (
                f"Data exists for this period ({count1:,} records) but the table join or "
                f"WHERE conditions return no results. Try broadening your criteria."
            ),
        }

    # ── Stage 3 ───────────────────────────────────────────────────────────────
    if having_filters:
        desc = _describe_filters(having_filters)
        return {
            "needs_clarification": True,
            "reason": (
                f"Data exists for this period ({count2:,} records after joining) but the "
                f"aggregate filter {desc} removes all results. Try relaxing this threshold."
            ),
        }

    return {"needs_clarification": False, "reason": "No data found matching all query criteria."}


async def _attempt_repair(
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

    prompt = REPAIR_PROMPT.format_messages(
        semantic_ir=json.dumps(first_ir, indent=2),
        schema_context=schema_summary,
        original_sql=first_sql,
        error_message=error_msg,
        prior_attempts=f"Attempt {repair_count}" if repair_count > 0 else "First attempt",
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

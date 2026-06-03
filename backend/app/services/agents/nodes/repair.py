"""LLM-based SQL repair for the executor node.

Called when executor gets a DB error and repair_count < MAX_REPAIR.
Uses Opus to rewrite the broken SQL from error + schema context.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import REASONING_DIRECTIVE_REPAIR, REPAIR_PROMPT

_PERFORMANCE_DIRECTIVE = """--- PERFORMANCE DIRECTIVES ---
If the error is a timeout or the query is slow, rewrite for Redshift performance as a senior DBA.
Business logic, tables, filters, and metric definitions must stay identical. Apply in order:
  a. Push WHERE filters into CTEs — never scan full tables and filter at the outer level.
  b. Aggregate before joining — one aggregation CTE per table, then join small results.
  c. Drop SELECT * and unused CTE columns.
  d. Replace DISTINCT with GROUP BY on explicit columns.
  e. Apply the LIMIT from the original QUERY SPECIFICATION; add LIMIT 100 if absent.
  f. Flatten CTEs that read the same source into one.
  g. CRITICAL — do NOT drop any JOIN that resolves an entity name filter. JOINs to reference
     tables (e.g., lpp.bank, lpp.counterparty, lpp.currency) translate the user's entity name
     (e.g., 'JPMorgan') into a DB code (e.g., 'BANK_JPM'). Removing them produces wrong data.
     If ENTITY VALUE MATCHES appears in SCHEMA REFERENCE, those columns MUST remain in the JOIN chain.
  h. CRITICAL — do NOT change or invent filter values. The QUERY INTENT section lists the
     RESOLVED filter values — use them verbatim in the rewrite. If QUERY INTENT says
     balance_type = 'CLOSING', keep exactly 'CLOSING'; never substitute 'Closing Balance' or
     any other variant. If QUERY INTENT says branch_ref = 'BR_JPM_NY', keep 'BR_JPM_NY'.
     Never fabricate reference codes that do not appear in QUERY INTENT or SCHEMA REFERENCE."""
from app.services.agents.sql_validator_logic import validate_sql
from app.services.agents.state import AnalyticsState


def _format_sql(sql: str) -> str:
    if not sql.strip():
        return sql
    try:
        import sqlglot
        return sqlglot.transpile(sql, read="redshift", write="redshift", pretty=True)[0]
    except Exception:
        return sql.strip()


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
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    logger.warning("repair | attempting repair | thread={} | repair_count={}", state["thread_id"], repair_count)

    anti_patterns = "(none)"
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
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
    schema_reference = _build_schema_reference_for_repair(sc, sql=first_sql)
    candidate_paths_section = _build_candidate_paths_section(first_ir)

    invalid_cols = _extract_invalid_columns(error_msg)
    invalid_cols_section = (
        f"\nINVALID COLUMNS — these do NOT exist in Redshift, do not use them under any name:\n"
        + "\n".join(f"  ✗ {c}" for c in invalid_cols)
        if invalid_cols else ""
    )

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

    if invalid_cols_section:
        prior_attempts_detail += invalid_cols_section

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
        performance_directive=_PERFORMANCE_DIRECTIVE if any(
            kw in e.lower()
            for e in errors
            for kw in ("timeout", "canceling", "statement timeout", "query_timeout")
        ) else "",
        anti_patterns=anti_patterns,
        candidate_paths_section=candidate_paths_section,
        reasoning_directive=REASONING_DIRECTIVE_REPAIR,
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
    repaired_sql = _format_sql(parse_tag(raw, "sql") or "")
    if not repaired_sql:
        logger.warning("repair | produced no SQL | thread={}", state["thread_id"])
        return None

    is_valid, val_error = validate_sql(repaired_sql)
    if not is_valid:
        logger.warning("repair | repaired SQL failed validation | thread={} | error={}", state["thread_id"], val_error)
        from app.services.agents.nodes.audit import write_anti_pattern
        asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="validation_error"))
        return None

    new_sql_list = [repaired_sql, *sql_list[1:]]
    logger.info("repair | succeeded | thread={}", state["thread_id"])
    from app.services.agents.nodes.audit import write_anti_pattern, write_audit_log
    asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="repair_input"))
    asyncio.create_task(write_audit_log(state, first_sql, 0, "repaired"))
    return {
        "sql_list": new_sql_list,
        "repair_count": repair_count + 1,
        "error": None,
    }


def _build_candidate_paths_section(ir_dict: dict) -> str:
    """Build CANDIDATE JOIN PATHS section from valid paths in the IR.

    Gives the repair LLM pre-validated ON clauses to use instead of guessing
    column names. Only includes paths that have non-empty join_clauses.
    """
    candidate_paths = ir_dict.get("candidate_join_paths") or []
    valid_paths = [p for p in candidate_paths if p.get("join_clauses")]
    if not valid_paths:
        return ""

    lines = ["CANDIDATE JOIN PATHS (use these for replacement ON clauses when the original is invalid):"]
    for p in valid_paths[:8]:
        clauses = " AND ".join(p.get("join_clauses") or [])
        tier = p.get("tier", "?")
        hops = p.get("hop_count", 1)
        from_fqn = p.get("from_fqn", "")
        to_fqn = p.get("to_fqn", "")
        lines.append(f"  [{tier}, {hops} hop] {from_fqn} → {to_fqn} | {clauses}")
    return "\n".join(lines)


def _extract_invalid_columns(error_msg: str) -> list[str]:
    """Parse Redshift/Postgres 'column X does not exist' errors and return the bad column names."""
    import re
    cols = []
    # Redshift: "column bank_account.bank_code does not exist"
    # Postgres: "column \"bank_code\" does not exist"
    for m in re.finditer(r'column\s+"?([^\s"]+)"?\s+does not exist', error_msg, re.IGNORECASE):
        col = m.group(1).strip('"')
        if col and col not in cols:
            cols.append(col)
    return cols


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
            filter_strs.append(f"{col} {op} '{val}'  ← DB CODE (use verbatim, do not change)")
        lines.append(f"Filters:    {', '.join(filter_strs)}")

    valid_joins = [c for c in join_clauses if c]
    if valid_joins:
        lines.append(f"Joins:      {', '.join(valid_joins)}")

    return "\n".join(lines)


def _build_schema_reference_for_repair(sc: dict, sql: str = "") -> str:
    """Build structured SCHEMA REFERENCE for repair.

    Tables referenced in the failing SQL are shown in full (all columns).
    All other tables are sampled (5 columns each) to keep the prompt focused.
    The 25-column hard cap is replaced with per-table prioritization.
    """
    tables = sc.get("tables", [])
    columns = sc.get("columns", [])

    lines = ["--- SCHEMA REFERENCE ---", ""]

    entity_hints = sc.get("entity_hints") or []
    if entity_hints:
        lines.append("ENTITY VALUE MATCHES (authoritative — these tokens matched schema vocabulary directly):")
        for eh in entity_hints[:5]:
            lines.append(
                f"  '{eh.get('token')}' → {eh.get('table_fqn')}.{eh.get('column')}"
                f" (matched: {str(eh.get('matched_value', ''))[:80]})"
                " — JOIN to this table and filter on this column"
            )
        lines.append("")

    if tables:
        lines.append("TABLES:")
        for t in tables[:10]:
            lines.append(f"  {t.get('fqn', '')}")
        lines.append("")

    if not columns:
        return "\n".join(lines)

    # Extract short table names from the failing SQL for prioritization
    sql_tables: set[str] = set()
    if sql:
        try:
            import sqlglot
            import sqlglot.expressions as exp
            for stmt in sqlglot.parse(sql, dialect="redshift"):
                for tbl in stmt.find_all(exp.Table):
                    if tbl.name:
                        sql_tables.add(tbl.name.lower())
        except Exception:
            pass

    # Group columns by table_fqn; SQL-referenced tables sort first
    from collections import defaultdict
    by_table: dict[str, list] = defaultdict(list)
    for c in columns:
        fqn = c.get("table_fqn", "")
        if fqn:
            by_table[fqn].append(c)

    lines.append("COLUMNS (tables in failing SQL shown in full; others sampled):")
    for fqn, cols in sorted(
        by_table.items(),
        key=lambda kv: (kv[0].rsplit(".", 1)[-1] not in sql_tables, kv[0]),
    ):
        short = fqn.rsplit(".", 1)[-1]
        in_sql = short in sql_tables
        shown = cols if in_sql else cols[:5]
        for c in shown:
            name = c.get("name", "")
            dtype = c.get("data_type", "")
            lines.append(f"  {fqn}.{name:<45} {dtype}")
        if not in_sql and len(cols) > 5:
            lines.append(f"  ... (+{len(cols) - 5} more columns in {fqn})")

    return "\n".join(lines)

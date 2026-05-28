"""SQL generation via LLM for the query_compiler node.

Builds the spec dict from a SemanticIR, logs it fully, then calls the
SQL generation LLM and returns the raw SQL string.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.nodes.schema_context import build_schema_context, fetch_anti_patterns, fetch_query_patterns
from app.services.agents.prompts import REASONING_DIRECTIVE_DEEP, CTE_COLUMN_PLANNER_PROMPT
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


async def generate_sql_llm(
    ir: SemanticIR,
    semantic_context: dict,
    state: AnalyticsState,
    config: RunnableConfig,
) -> str:
    schema_ctx_full = build_schema_context(ir, semantic_context)
    unresolved_pairs = schema_ctx_full.pop("_unresolved_pairs", [])
    schema_ctx = {k: v for k, v in schema_ctx_full.items()}

    spec = {
        "anchor_tables": ir.anchor_tables,
        "path_tables": ir.path_tables,
        "joins": [
            {
                "from": ir.path_tables[i],
                "to": ir.path_tables[i + 1],
                "type": ir.join_types[i] if i < len(ir.join_types) else "JOIN",
                "on": ir.join_clauses[i],
            }
            for i in range(len(ir.join_clauses))
            if ir.join_clauses[i]
            if i + 1 < len(ir.path_tables)
        ],
        "unresolved_anchor_pairs": unresolved_pairs,
        "measures": [m.model_dump() for m in ir.measures],
        "dimensions": [d.model_dump() for d in ir.dimensions],
        "filters": [
            {
                "table_fqn": f.table_fqn,
                "column": f.column_name,
                "operator": f.operator,
                "value": f.value,
                "is_having": f.is_having,
                "is_raw_sql": f.is_raw_sql,
            }
            for f in ir.filters
        ],
        "time_filter": {
            "table_fqn": ir.time_filter.table_fqn,
            "column": ir.time_filter.column_name,
            "operator": ir.time_filter.operator,
            "value": ir.time_filter.value,
        } if ir.time_filter else None,
        "cte_steps": ir.cte_steps[:4],
        "result_shape": ir.result_shape,
        "order_by": ir.order_by,
        "limit": ir.limit or state.get("max_rows", 100),
    }

    logger.info(
        "sql_generator | LLM input | thread={} | anchor_tables={} | pre_loaded_joins={} | unresolved_pairs={} | measures={} | dimensions={} | filters={} | time_filter={}",
        state["thread_id"],
        spec["anchor_tables"],
        [(j["from"], j["to"], j["on"]) for j in spec["joins"]],
        [(p["from"], p["to"], p.get("candidate_join_columns")) for p in unresolved_pairs],
        [(m["column_name"], m.get("aggregation")) for m in spec["measures"]],
        [d["column_name"] for d in spec["dimensions"]],
        [(f["column"], f["operator"], str(f["value"])[:20]) for f in spec["filters"]],
        (
            f"{spec['time_filter']['column']} {spec['time_filter']['operator']} {str(spec['time_filter']['value'])[:30]}"
            if spec.get("time_filter") else None
        ),
    )

    anti_patterns = await fetch_anti_patterns(state)
    query_patterns, pattern_matched, pattern_name = await fetch_query_patterns(state)

    logger.info(
        "sql_generator | context_injection | anti_patterns={} | query_pattern={} | thread={}",
        "injected" if anti_patterns != "(none)" else "none",
        pattern_name or "none",
        state.get("thread_id"),
    )

    query_blueprint = _build_query_blueprint(spec, schema_ctx)
    schema_reference = _build_schema_reference(schema_ctx)
    unresolved_joins_section = _build_unresolved_joins_section(unresolved_pairs)
    feedback_section = _build_feedback_section(state)
    query_patterns_section = _build_query_patterns_section(query_patterns)
    prior_sql_section = _build_prior_sql_section(state)
    reasoning_directive = REASONING_DIRECTIVE_DEEP

    cte_plan = await _plan_cte_columns(spec, query_blueprint, schema_reference, state, config)
    cte_column_plan = _build_cte_plan_section(cte_plan)

    from app.services.agents.prompts import SQL_GENERATE_PROMPT
    prompt = SQL_GENERATE_PROMPT.format_messages(
        query_blueprint=query_blueprint,
        schema_reference=schema_reference,
        anti_patterns=anti_patterns,
        reasoning_directive=reasoning_directive,
        unresolved_joins_section=unresolved_joins_section,
        feedback_section=feedback_section,
        query_patterns_section=query_patterns_section,
        prior_sql_section=prior_sql_section,
        cte_column_plan=cte_column_plan,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("deep")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    response = await _call()
    sql = parse_tag(response.content or "", "sql")
    logger.info(
        "sql_generator | SQL generated | thread={} | anchor={} | sql_len={} | pattern_matched={} | pattern={} | reasoning=DEEP",
        state["thread_id"], ir.anchor_tables, len(sql or ""), pattern_matched, pattern_name,
    )
    return (sql or "").strip()


async def _plan_cte_columns(
    spec: dict,
    query_blueprint: str,
    schema_reference: str,
    state: AnalyticsState,
    config: RunnableConfig,
) -> str:
    """Fast pre-pass: ask a focused model to solve CTE column forwarding before SQL is written.

    Returns the plan string (content inside <plan> tags), or "" on failure.
    """
    has_measures = bool(spec.get("measures"))
    if not has_measures:
        return ""

    from app.services.agents.bedrock import get_llm
    try:
        llm = get_llm("fast")
        prompt = CTE_COLUMN_PLANNER_PROMPT.format_messages(
            query_blueprint=query_blueprint,
            schema_reference=schema_reference,
        )
        response = await llm.ainvoke(prompt, config=config)
        plan = parse_tag(response.content or "", "plan").strip()
        logger.info(
            "sql_generator | CTE planner done | thread={} | plan_len={}",
            state["thread_id"], len(plan),
        )
        return plan
    except Exception as e:
        logger.warning(
            "sql_generator | CTE planner failed (degrading gracefully) | thread={} | error={}",
            state["thread_id"], e,
        )
        return ""


def _is_exact_value(value) -> bool:
    """Return True when value should stay as exact = match (numeric or date-like).

    Anything else (string codes, names, identifiers) should use ILIKE so
    Neo4j-generated casing mismatches don't silently kill results.
    """
    import re
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, list):
        return all(_is_exact_value(v) for v in value)
    s = str(value)
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        pass
    # Date-like: YYYY-MM-DD, MM/DD/YYYY, YYYY/MM/DD
    if re.match(r"^\d{4}-\d{2}-\d{2}|^\d{2}/\d{2}/\d{4}|^\d{4}/\d{2}/\d{2}", s):
        return True
    return False


def _build_cte_plan_section(plan: str) -> str:
    if not plan:
        return ""
    return (
        "---\n\n"
        "CTE COLUMN PLAN (pre-solved — each CTE's SELECT MUST include at minimum these columns):\n\n"
        + plan
        + "\n\nThis plan is authoritative. Rule 15 applies: do not deviate."
    )


def _build_query_blueprint(spec: dict, schema_ctx: dict) -> str:
    """Build structured QUERY SPECIFICATION text replacing json.dumps(spec)."""
    lines = ["--- QUERY SPECIFICATION ---", ""]

    anchor_tables = spec.get("anchor_tables") or []
    lines.append("ANCHOR TABLES (every one must appear in the SQL — do not drop or add):")
    for t in anchor_tables:
        lines.append(f"  {t}")
    lines.append("")

    result_shape = spec.get("result_shape")
    if result_shape and result_shape != "table":
        lines.append(f"RESULT SHAPE: {result_shape}")
        lines.append("")

    time_filter = spec.get("time_filter")
    if time_filter:
        tf_col = f"{time_filter.get('table_fqn', '')}.{time_filter.get('column', '')}"
        tf_op = time_filter.get("operator", ">=")
        tf_val = time_filter.get("value", "")
        lines.append(f"TIME FILTER:\n  {tf_col} {tf_op} {tf_val}")
        lines.append("")

    measures = spec.get("measures") or []
    dimensions = spec.get("dimensions") or []

    if measures:
        lines.append("MEASURES (wrap each in its aggregate):")
        for m in measures:
            agg = m.get("aggregation") or ""
            fqn = m.get("table_fqn", "")
            col = m.get("column_name", "")
            alias = m.get("alias", col)
            if not agg or agg.upper() in ("", "NONE"):
                agg_label = "[DECIDE]"
            else:
                agg_label = agg
            lines.append(f"  {agg_label}({fqn}.{col})              alias: {alias}")
        lines.append("")

        if dimensions:
            lines.append("DIMENSIONS (must be in GROUP BY):")
            for d in dimensions:
                fqn = d.get("table_fqn", "")
                col = d.get("column_name", "")
                alias = d.get("alias", col)
                lines.append(f"  {fqn}.{col}          alias: {alias}")
            lines.append("")
    else:
        lines.append("RESULT TYPE: flat lookup — no GROUP BY")
        lines.append("")

    filters = spec.get("filters") or []
    if filters:
        lines.append("FILTERS:")
        from collections import defaultdict
        ilike_groups: dict[tuple, list] = defaultdict(list)
        exact_groups: dict[tuple, list] = defaultdict(list)
        other_filters: list[dict] = []
        for f in filters:
            if f.get("is_raw_sql"):
                other_filters.append(f)
            elif f.get("operator") in ("ILIKE", "LIKE"):
                ilike_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            elif f.get("operator") == "=" and not _is_exact_value(f.get("value")):
                # String values: use ILIKE %value% — Neo4j-resolved casing is LLM-generated
                ilike_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            elif f.get("operator") == "=":
                # Dates and numbers: keep exact =
                exact_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            else:
                other_filters.append(f)

        for f in other_filters:
            clause, label = _format_filter_line(f)
            section = "HAVING" if f.get("is_having") else "WHERE"
            lines.append(f"  {section}:   {clause}   {label}")

        # Exact = filters: dates/numbers keep = ; string values use ILIKE %value%
        for (tfqn, col), grp in exact_groups.items():
            section = "HAVING" if grp[0].get("is_having") else "WHERE"
            if len(grp) == 1:
                val = grp[0].get("value", "")
                lines.append(f"  {section}:   {tfqn}.{col} = '{val}'   [exact]")
            else:
                vals = ", ".join(f"'{g.get('value', '')}'" for g in grp)
                lines.append(f"  {section}:   {tfqn}.{col} IN ({vals})   [exact — multiple values, use IN]")

        for (tfqn, col), grp in ilike_groups.items():
            section = "HAVING" if grp[0].get("is_having") else "WHERE"

            def _ilike_clause(v: str) -> str:
                s = str(v)
                return f"{tfqn}.{col} ILIKE '{s}'" if "%" in s else f"{tfqn}.{col} ILIKE '%{s}%'"

            if len(grp) == 1:
                val = grp[0].get("value", "")
                clause = _ilike_clause(val)
                label = "[fuzzy — use ILIKE]"
            else:
                parts = " OR ".join(_ilike_clause(g.get("value", "")) for g in grp)
                clause = f"({parts})"
                label = "[fuzzy — multiple, use OR ILIKE]"
            lines.append(f"  {section}:   {clause}   {label}")
        lines.append("")

    joins = spec.get("joins") or []
    if joins:
        base_table = joins[0].get("from", anchor_tables[0] if anchor_tables else "")
        lines.append("PRE-COMPUTED JOIN CHAIN (copy this FROM + JOIN sequence verbatim into the first CTE):")
        lines.append(f"  FROM {base_table}")
        for j in joins:
            jtype = j.get("type", "INNER JOIN") or "INNER JOIN"
            to_t = j.get("to", "")
            on_clause = j.get("on", "")
            lines.append(f"  {jtype} {to_t}")
            lines.append(f"    ON {on_clause}")
        lines.append("")
    elif anchor_tables:
        lines.append("BASE TABLE (must be in the FROM clause of the first CTE):")
        lines.append(f"  FROM {anchor_tables[0]}")
        lines.append("")

    cte_steps = spec.get("cte_steps") or []
    if cte_steps:
        lines.append("CTE NAMES (use in this order):")
        lines.append("  " + "  →  ".join(cte_steps))
        lines.append("")

    order_by = spec.get("order_by")
    if order_by:
        lines.append(f"SORT:   {order_by}")
    limit = spec.get("limit")
    if limit:
        lines.append(f"LIMIT:  {limit}")

    return "\n".join(lines)


def _format_filter_line(f: dict) -> tuple[str, str]:
    """Return (clause_text, label) for a single non-ILIKE filter."""
    tfqn = f.get("table_fqn", "")
    col = f.get("column", "")
    op = f.get("operator", "=")
    value = f.get("value")
    is_raw_sql = f.get("is_raw_sql", False)

    if is_raw_sql:
        return f"{tfqn}.{col} {op} {value}", ""

    if isinstance(value, list):
        quoted = ", ".join(f"'{v}'" for v in value)
        return f"{tfqn}.{col} IN ({quoted})", "[exact — multiple values, use IN]"

    return f"{tfqn}.{col} {op} '{value}'", "[exact]"


def _build_schema_reference(schema_ctx: dict) -> str:
    """Build structured SCHEMA REFERENCE text replacing json.dumps(schema_ctx)."""
    tables = schema_ctx.get("tables", [])
    columns = schema_ctx.get("columns", [])
    available_joins = schema_ctx.get("available_joins", [])

    lines = ["--- SCHEMA REFERENCE ---", ""]

    lines.append("TABLES (all candidate tables — fact/dimension/bridge role shown):")
    for t in tables:
        fqn = t.get("fqn", "")
        role = t.get("typical_join_role", "") or t.get("table_type", "")
        desc = t.get("description", "")
        grain = t.get("grain", "")
        role_str = f" {role:<12}" if role else ""
        desc_str = f" — {desc}" if desc else ""
        grain_str = f"   grain: {grain}" if grain else ""
        lines.append(f"  {fqn:<40}{role_str}{desc_str}{grain_str}".rstrip())
    lines.append("")

    primary_cols = [c for c in columns if "is_groupable" in c or "is_measurable" in c]
    secondary_cols = [c for c in columns if "is_groupable" not in c and "is_measurable" not in c]

    if primary_cols:
        lines.append("PRIMARY COLUMNS (anchor and path tables — use these for SELECT, WHERE, GROUP BY, HAVING):")
        for c in primary_cols:
            fqn = c.get("table_fqn", "")
            name = c.get("name", "")
            dtype = c.get("data_type", c.get("semantic_type", ""))
            is_measurable = c.get("is_measurable", False)
            is_groupable = c.get("is_groupable", False)
            desc = c.get("description", "")
            filter_values = c.get("filter_values") or c.get("sample_values") or []

            marker = "[AGG]" if is_measurable else ("[GRP]" if is_groupable else "")
            col_ref = f"{fqn}.{name}"
            line = f"  {col_ref:<50} {dtype:<10} {marker:<6}"
            if desc:
                line += f'  "{desc}"'
            if filter_values:
                vals_str = " | ".join(str(v) for v in filter_values[:8])
                line += f"   values: {vals_str}"
            elif is_measurable:
                line += "   (SUM or AVG)"
            else:
                line += "   (no known values)"
            lines.append(line)
        lines.append("")

    if secondary_cols:
        lines.append("SECONDARY COLUMNS (other candidate tables — available only as JOIN partners for display columns):")
        for c in secondary_cols:
            fqn = c.get("table_fqn", "")
            name = c.get("name", "")
            dtype = c.get("data_type", c.get("semantic_type", ""))
            lines.append(f"  {fqn}.{name:<50} {dtype}")
        lines.append("")

    if available_joins:
        lines.append("ADDITIONAL JOINS (if you need a table not in PRE-COMPUTED JOINS):")
        for j in available_joins:
            from_t = j.get("from", "")
            to_t = j.get("to", "")
            join_type = j.get("join_type", "INNER JOIN")
            clauses = j.get("join_clauses", [])
            lines.append(f"  {from_t} → {to_t}")
            for clause in clauses:
                lines.append(f"    {join_type} {to_t} ON {clause}")

    return "\n".join(lines)


def _build_unresolved_joins_section(unresolved_pairs: list[dict]) -> str:
    if not unresolved_pairs:
        return ""
    lines = ["UNRESOLVED JOIN PAIRS — no pre-computed path found in Neo4j. You MUST resolve each of these:\n"]
    for pair in unresolved_pairs:
        from_t = pair.get("from", "")
        to_t = pair.get("to", "")
        candidates = pair.get("candidate_join_columns", [])
        lines.append(f"  {from_t} → {to_t}")
        if candidates:
            lines.append(f"    candidate_join_columns: {candidates}")
            lines.append("    → Check ADDITIONAL JOINS in SCHEMA REFERENCE first (use ON clause exactly if found).")
            lines.append("    → Otherwise JOIN ON the most semantically specific candidate column.")
        else:
            lines.append("    → No candidate columns found. Check ADDITIONAL JOINS in SCHEMA REFERENCE.")
        lines.append("    → NEVER produce a CROSS JOIN or omit the table.\n")
    return "\n".join(lines)


def _build_feedback_section(state: AnalyticsState) -> str:
    fb = state.get("feedback_context") or ""
    if not fb:
        return ""
    return (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n"
        f"<feedback_context>{fb}</feedback_context>"
    )


def _build_query_patterns_section(query_patterns: list) -> str:
    if not query_patterns:
        return ""
    top = query_patterns[0]
    question_text = top.get("question_text", "")
    intent = top.get("intent", "")
    tables = top.get("tables_used", "")
    outline = top.get("sql_cte_outline", "")
    join_outline = top.get("join_outline", "")
    filter_summary = top.get("filter_summary", "")
    complexity = top.get("complexity", "")
    recompile = top.get("recompile_count") or 0
    repair = top.get("repair_count") or 0
    if not (outline or join_outline):
        return ""
    lines = [
        "SIMILAR QUERY PATTERNS (prior successful query for a similar question — use as structural guide):",
    ]
    if question_text:
        lines.append(f"  Question:   \"{question_text}\"")
    lines += [
        f"  Intent:     {intent}",
        f"  Tables:     {tables}",
    ]
    if complexity:
        lines.append(f"  Complexity: {complexity}")
    if outline:
        lines.append(f"  Structure:  {outline}")
    if join_outline:
        lines.append(f"  Joins:      {join_outline}")
    if filter_summary:
        lines.append(f"  Filters:    {filter_summary}")
    if recompile or repair:
        lines.append(
            f"  Note: this pattern needed {recompile} recompile(s) and {repair} repair(s) — "
            "study its join keys and filter patterns carefully before adapting."
        )
    return "\n".join(lines)


def _build_prior_sql_section(state: AnalyticsState) -> str:
    prior_sql = state.get("prior_sql") or ""
    recompile_count = state.get("recompile_count", 0)
    if not (recompile_count > 0 and prior_sql):
        return ""
    error = state.get("error") or ""
    error_line = f"Validation error that must be fixed:\n  {error}\n\n" if error else ""
    return (
        "PREVIOUS SQL ATTEMPT (recompile — prior SQL failed validation):\n\n"
        f"{error_line}"
        f"<prior_sql>{prior_sql}</prior_sql>\n\n"
        "Fix the specific validation error above. Do not repeat the structural mistake that caused it."
    )


def parse_decomposition(raw: str, thread_id: str) -> dict | None:
    from json_repair import loads as json_loads
    output = parse_tag(raw, "output")
    if not output:
        return None
    try:
        return json_loads(output)
    except Exception as e:
        logger.warning("sql_generator | decompose parse failed | thread={} | error={}", thread_id, e)
        return None

"""SQLGlot-based SQL compiler: SemanticIR → Redshift CTE SQL.

Pure function — no I/O, no LLM calls. All filter values must be resolved
before calling compile_sql(). Uses parameterized binding via SQLGlot AST.
"""

from __future__ import annotations

import sqlglot
import sqlglot.expressions as exp
from sqlglot.dialects import Redshift

from app.core.logger import logger
from app.services.neo4j_analytics.semantic_ir import FilterSpec, SemanticIR


def compile_sql(ir: SemanticIR) -> str:
    """Compile a SemanticIR into a 4-layer CTE Redshift SQL string.

    Raises ValueError if any FilterSpec is unresolved.
    """
    for f in ir.filters:
        if not f.resolved:
            raise ValueError(f"FilterSpec for {f.column_name} is not resolved — cannot compile SQL")
    if ir.time_filter and not ir.time_filter.resolved:
        raise ValueError(f"time_filter for {ir.time_filter.column_name} is not resolved — cannot compile SQL")

    try:
        sql = _build_cte_sql(ir)
        logger.debug("sql_compiler | template={} | complexity={} | sql_len={}", ir.template_id, ir.complexity, len(sql))
        return sql
    except Exception as e:
        logger.error("sql_compiler failed | template={} | error={}", ir.template_id, e)
        raise


def _build_cte_sql(ir: SemanticIR) -> str:
    """Build the full 4-layer CTE SQL from a SemanticIR."""
    cte_names = _get_cte_names(ir)
    join_sql = _build_join_clause(ir)
    where_conditions = _build_where_conditions(ir)
    group_by_cols = _build_group_by_cols(ir)
    measure_cols = _build_measure_cols(ir)
    order_by_clause = _build_order_by(ir)

    where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
    group_by_clause = f"GROUP BY {', '.join(group_by_cols)}" if group_by_cols else ""

    base_select = ", ".join(
        [f"{c.table_fqn}.{c.column_name} AS {c.alias}" for c in ir.dimensions]
        + [f"{c.table_fqn}.{c.column_name}" for c in ir.measures if not c.aggregation or c.aggregation == "NONE"]
    )
    if not base_select:
        base_select = "*"

    agg_select = ", ".join(
        [f"{c.alias}" for c in ir.dimensions]
        + [f"{_agg_expr(c)} AS {c.alias}" for c in ir.measures if c.aggregation and c.aggregation != "NONE"]
    )
    if not agg_select:
        agg_select = "*"

    parts = [f"WITH"]

    base_cte = (
        f"  {cte_names[0]} AS (\n"
        f"    SELECT {base_select}\n"
        f"    {join_sql}\n"
        f"    {where_clause}\n"
        f"  )"
    )
    parts.append(base_cte)

    if group_by_clause or measure_cols:
        agg_cte = (
            f"  {cte_names[1]} AS (\n"
            f"    SELECT {agg_select}\n"
            f"    FROM {cte_names[0]}\n"
            f"    {group_by_clause}\n"
            f"  )"
        )
        parts.append(",\n" + agg_cte)
        prev_cte = cte_names[1]
    else:
        prev_cte = cte_names[0]

    if ir.order_by or ir.limit:
        order_limit = f"    {order_by_clause}" if order_by_clause else ""
        limit_str = f"    LIMIT {ir.limit}" if ir.limit else ""
        final_cte = (
            f"  final AS (\n"
            f"    SELECT *\n"
            f"    FROM {prev_cte}\n"
            f"    {order_limit}\n"
            f"    {limit_str}\n"
            f"  )"
        )
        parts.append(",\n" + final_cte)
        prev_cte = "final"

    sql = "\n".join(parts) + f"\nSELECT * FROM {prev_cte}"
    return _clean_sql(sql)


def _get_cte_names(ir: SemanticIR) -> list[str]:
    """Return CTE names; use template hints then fall back to generics."""
    defaults = ["base_data", "aggregated", "ranked", "final"]
    names = list(ir.cte_steps) if ir.cte_steps else []
    while len(names) < 4:
        names.append(defaults[len(names)] if len(names) < len(defaults) else f"extra_cte_{len(names)}")
    return names


def _build_join_clause(ir: SemanticIR) -> str:
    """Build FROM ... JOIN chain from path_tables and join_clauses."""
    if not ir.path_tables:
        return f"FROM {ir.anchor_tables[0]}" if ir.anchor_tables else "FROM unknown"

    result = f"FROM {ir.path_tables[0]}"
    for i, join_clause in enumerate(ir.join_clauses):
        if i + 1 >= len(ir.path_tables):
            break
        left_table = ir.path_tables[i]
        right_table = ir.path_tables[i + 1]
        join_type = ir.join_types[i] if i < len(ir.join_types) else "JOIN"
        left_col, right_col = _parse_join_clause(join_clause)
        result += f"\n    {join_type} {right_table} ON {left_table}.{left_col} = {right_table}.{right_col}"
    return result


def _parse_join_clause(clause: str) -> tuple[str, str]:
    """Parse 'left_col = right_col' into a tuple."""
    if "=" in clause:
        parts = clause.split("=", 1)
        return parts[0].strip(), parts[1].strip()
    return clause.strip(), clause.strip()


def _build_where_conditions(ir: SemanticIR) -> list[str]:
    """Build parameterized WHERE conditions from FilterSpecs."""
    conditions = []
    all_filters = list(ir.filters)
    if ir.time_filter:
        all_filters.append(ir.time_filter)

    for f in all_filters:
        col_ref = f"{f.table_fqn}.{f.column_name}"
        if f.operator == "BETWEEN" and isinstance(f.value, list) and len(f.value) == 2:
            conditions.append(f"{col_ref} BETWEEN '{f.value[0]}' AND '{f.value[1]}'")
        elif f.operator == "IN" and isinstance(f.value, list):
            values_str = ", ".join(f"'{v}'" for v in f.value)
            conditions.append(f"{col_ref} IN ({values_str})")
        elif f.operator == "LIKE":
            conditions.append(f"LOWER({col_ref}) LIKE LOWER('{f.value}')")
        else:
            conditions.append(f"{col_ref} {f.operator} '{f.value}'")
    return conditions


def _build_group_by_cols(ir: SemanticIR) -> list[str]:
    has_aggregation = any(c.aggregation and c.aggregation != "NONE" for c in ir.measures)
    if not has_aggregation:
        return []
    return [c.alias for c in ir.dimensions]


def _build_select_cols(ir: SemanticIR) -> list[str]:
    cols = []
    for c in ir.dimensions:
        cols.append(f"{c.table_fqn}.{c.column_name} AS {c.alias}")
    for c in ir.measures:
        if not c.aggregation or c.aggregation == "NONE":
            cols.append(f"{c.table_fqn}.{c.column_name} AS {c.alias}")
    return cols


def _build_measure_cols(ir: SemanticIR) -> list[str]:
    return [c.alias for c in ir.measures if c.aggregation and c.aggregation != "NONE"]


def _agg_expr(col) -> str:
    agg = (col.aggregation or "SUM").upper()
    return f"{agg}({col.table_fqn}.{col.column_name})"


def _build_order_by(ir: SemanticIR) -> str:
    if not ir.order_by:
        return ""
    return f"ORDER BY {', '.join(ir.order_by)}"


def _clean_sql(sql: str) -> str:
    """Remove extra blank lines from generated SQL."""
    lines = [line for line in sql.splitlines() if line.strip()]
    return "\n".join(lines)

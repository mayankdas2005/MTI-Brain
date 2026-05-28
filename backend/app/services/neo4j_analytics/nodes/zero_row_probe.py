"""Zero-row diagnosis for the executor node.

Three-stage probe when execution returns 0 rows:
  Stage 1 — time filter only (is there any data for this period?)
  Stage 2 — time + JOINs + WHERE filters (are the join/filter conditions too narrow?)
  Stage 3 — HAVING by elimination (is an aggregate threshold culling all results?)
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState


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


def _quote_scalar(v: str) -> str:
    try:
        float(v)
        return str(v)
    except (ValueError, TypeError):
        return f"'{v}'"


def _rewrite_table_aliases(text: str, aliases: dict[str, str]) -> str:
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


async def zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    """Three-stage zero-row diagnosis.

    Stage 1 — time-only: SELECT COUNT(*) FROM time_table WHERE time_col op value
    Stage 2 — time + JOINs + WHERE filters
    Stage 3 — HAVING by elimination (no extra DB call)
    """
    if not ir or not ir.anchor_tables:
        return {"needs_clarification": False, "reason": "No data found for the requested query."}

    from app.services.neo4j_analytics.redshift_client import execute_query
    time_filter = ir.time_filter

    if not time_filter or not time_filter.resolved:
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    time_table = time_filter.table_fqn
    s1_cond = _build_filter_condition(time_filter.column_name, time_filter)
    if not s1_cond:
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    try:
        _, rows = await execute_query(
            f"SELECT COUNT(*) AS cnt FROM {time_table} WHERE {s1_cond}",
            timeout_s=60, thread_id=state["thread_id"],
        )
        count1 = int(rows[0][0]) if rows and rows[0] else 0
    except Exception as e:
        logger.warning("zero_row_probe | stage 1 failed | error={}", e)
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}

    if count1 == 0:
        return {
            "needs_clarification": False,
            "reason": f"No data exists in `{time_table}` for the requested time period.",
        }

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    where_filters = [f for f in (ir.filters or []) if not f.is_having and f.resolved]
    having_filters = [f for f in (ir.filters or []) if f.is_having and f.resolved]
    count2 = count1

    from_info = _build_probe_from_clause(ir)
    if from_info:
        from_clause, aliases = from_info

        t_alias = aliases.get(time_filter.table_fqn)
        t_col_ref = f"{t_alias}.{time_filter.column_name}" if t_alias else time_filter.column_name
        s2_time_cond = _build_filter_condition(t_col_ref, time_filter)

        where_parts = [s2_time_cond] if s2_time_cond else []
        for f in where_filters:
            f_alias = aliases.get(f.table_fqn)
            col_ref = f"{f_alias}.{f.column_name}" if f_alias else f.column_name
            cond = _build_filter_condition(col_ref, f)
            if cond:
                where_parts.append(cond)

        if where_parts:
            stage2_sql = f"SELECT COUNT(*) AS cnt\nFROM {from_clause}\nWHERE {' AND '.join(where_parts)}"
            try:
                _, rows = await execute_query(stage2_sql, timeout_s=60, thread_id=state["thread_id"])
                count2 = int(rows[0][0]) if rows and rows[0] else 0
            except Exception as e:
                logger.warning("zero_row_probe | stage 2 failed | error={}", e)

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

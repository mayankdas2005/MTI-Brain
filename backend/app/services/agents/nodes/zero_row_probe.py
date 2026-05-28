"""Zero-row diagnosis for the executor node.

Four-stage progressive probe when execution returns 0 rows:
  Stage 1 — No time filter, WHERE filters only (OR same-col, AND across columns)
  Stage 2 — No time filter, OR ALL filter conditions together (maximum relaxation)
  Stage 3 — No filters at all (bare FROM + JOINs only)
  Stage 4 — HAVING by elimination (no extra DB call)

Each stage that returns rows runs a small sample SELECT so we confirm real data
exists and can report the actual row count.
"""

from __future__ import annotations

from collections import defaultdict

from app.core.logger import logger
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _quote_scalar(v) -> str:
    try:
        float(str(v))
        return str(v)
    except (ValueError, TypeError):
        return f"'{v}'"


def _rewrite_table_aliases(text: str, aliases: dict[str, str]) -> str:
    result = text
    for fqn, alias in sorted(aliases.items(), key=lambda x: -len(x[0])):
        result = result.replace(f"{fqn}.", f"{alias}.")
    return result


def _build_probe_from_clause(ir: SemanticIR) -> tuple[str, dict[str, str]] | None:
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


def _col_ref(f, aliases: dict[str, str]) -> str:
    alias = aliases.get(f.table_fqn)
    return f"{alias}.{f.column_name}" if alias else f"{f.table_fqn}.{f.column_name}"


def _and_conditions(filter_list: list, aliases: dict[str, str]) -> str:
    """OR within same column, AND across columns."""
    groups: dict[tuple, list] = defaultdict(list)
    for f in filter_list:
        groups[(f.table_fqn, f.column_name)].append(f)
    parts = []
    for (fqn, col), grp in groups.items():
        ref = _col_ref(grp[0], aliases)
        conds = [c for f in grp if (c := _build_filter_condition(ref, f))]
        if len(conds) == 1:
            parts.append(conds[0])
        elif conds:
            parts.append(f"({' OR '.join(conds)})")
    return " AND ".join(parts)


def _or_conditions(filter_list: list, aliases: dict[str, str]) -> str:
    """All conditions ORed together — maximum relaxation."""
    conds = []
    for f in filter_list:
        ref = _col_ref(f, aliases)
        c = _build_filter_condition(ref, f)
        if c:
            conds.append(c)
    return f"({' OR '.join(conds)})" if conds else ""


# ── Main probe ────────────────────────────────────────────────────────────────

async def zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    if not ir or not ir.anchor_tables:
        return {"needs_clarification": False, "reason": "No data found for the requested query."}

    from app.services.agents.redshift_client import execute_query

    where_filters = [f for f in (ir.filters or []) if not f.is_having and f.resolved]
    having_filters = [f for f in (ir.filters or []) if f.is_having and f.resolved]
    time_filter = ir.time_filter if (ir.time_filter and ir.time_filter.resolved) else None
    sample_limit = min(ir.limit or 5, 5)

    from_info = _build_probe_from_clause(ir)
    if not from_info:
        return {"needs_clarification": False, "reason": "No data found matching the query criteria."}
    from_clause, aliases = from_info

    async def _count(where_clause: str) -> int:
        sql = f"SELECT COUNT(*) AS cnt\nFROM {from_clause}"
        if where_clause:
            sql += f"\nWHERE {where_clause}"
        try:
            _, rows = await execute_query(sql, timeout_s=30, thread_id=state["thread_id"])
            return int(rows[0][0]) if rows and rows[0] else 0
        except Exception as e:
            logger.warning("zero_row_probe | count query failed | sql_preview={} | error={}", sql[:200], e)
            return -1

    async def _sample(where_clause: str) -> int:
        """Run SELECT with limit to confirm actual fetchable rows, returns row count."""
        sql = f"SELECT *\nFROM {from_clause}"
        if where_clause:
            sql += f"\nWHERE {where_clause}"
        sql += f"\nLIMIT {sample_limit}"
        try:
            _, rows = await execute_query(sql, timeout_s=30, thread_id=state["thread_id"])
            return len(rows)
        except Exception as e:
            logger.warning("zero_row_probe | sample query failed | error={}", e)
            return 0

    # ── Stage 1: remove time, WHERE filters with OR same-col / AND across ────
    if where_filters:
        s1_where = _and_conditions(where_filters, aliases)
        s1_count = await _count(s1_where)
        logger.info(
            "zero_row_probe | stage1 (no time, AND filters) | count={} | thread={}",
            s1_count, state["thread_id"],
        )
        if s1_count > 0:
            await _sample(s1_where)
            time_str = f" for {time_filter.value}" if time_filter else ""
            return {
                "needs_clarification": True,
                "reason": (
                    f"Found {s1_count:,} record(s) matching your filter values but not{time_str} "
                    "the requested time period. The time range is too narrow — try a broader date range."
                ),
            }

        # ── Stage 2: no time, OR ALL filter conditions across columns ─────────
        distinct_cols = len({(f.table_fqn, f.column_name) for f in where_filters})
        if distinct_cols > 1:
            s2_where = _or_conditions(where_filters, aliases)
            if s2_where:
                s2_count = await _count(s2_where)
                logger.info(
                    "zero_row_probe | stage2 (no time, OR all filters) | count={} | thread={}",
                    s2_count, state["thread_id"],
                )
                if s2_count > 0:
                    await _sample(s2_where)
                    desc = _describe_filters(where_filters)
                    return {
                        "needs_clarification": True,
                        "reason": (
                            f"Found {s2_count:,} record(s) when OR-ing all filter conditions, "
                            f"but 0 when AND-ing them. The combination of {desc} is too restrictive — "
                            "each value exists individually but not together. "
                            "Try removing one filter at a time."
                        ),
                    }

    # ── Stage 3: no filters at all (bare FROM + JOINs) ───────────────────────
    s3_count = await _count("")
    logger.info(
        "zero_row_probe | stage3 (bare joins, no filters) | count={} | thread={}",
        s3_count, state["thread_id"],
    )
    if s3_count > 0:
        await _sample("")
        if where_filters:
            desc = _describe_filters(where_filters)
            time_msg = " and time filter" if time_filter else ""
            return {
                "needs_clarification": True,
                "reason": (
                    f"The joined tables contain {s3_count:,} record(s) but all applied filters"
                    f"{time_msg} ({desc}) return 0 results. "
                    "The filter values may not match what is stored — check exact spelling and casing."
                ),
            }
        elif time_filter:
            return {
                "needs_clarification": True,
                "reason": (
                    f"The tables contain {s3_count:,} record(s) but none fall in the requested "
                    "time period. Try a broader date range."
                ),
            }
        return {
            "needs_clarification": False,
            "reason": "The tables have data but no rows matched your query criteria.",
        }

    if s3_count == 0:
        return {
            "needs_clarification": False,
            "reason": (
                "The joined tables return 0 rows — the join conditions may be incorrect "
                "or the tables share no matching keys for this query."
            ),
        }

    # ── Stage 4: HAVING by elimination (no extra DB call) ────────────────────
    if having_filters:
        desc = _describe_filters(having_filters)
        return {
            "needs_clarification": True,
            "reason": (
                f"Data exists but the aggregate filter {desc} removes all results. "
                "Try relaxing this threshold."
            ),
        }

    return {"needs_clarification": False, "reason": "No data found matching all query criteria."}

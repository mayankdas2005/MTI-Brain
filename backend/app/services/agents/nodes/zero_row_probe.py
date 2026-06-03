"""Zero-row diagnosis for the executor node.

Four-stage progressive probe when execution returns 0 rows:
  Stage 1 — No time filter, WHERE filters only (OR same-col, AND across columns)
  Stage 2 — No time filter, OR ALL filter conditions together (maximum relaxation)
  Stage 3 — Full SQL with ALL WHERE/HAVING stripped (handles complex multi-CTE queries)
  Stage 3b — Individual table COUNT(*) probes to distinguish empty table vs bad join
  Stage 4 — HAVING by elimination (no extra DB call)

Every return dict includes a probe_type key:
  "time_filter"      — time range too narrow
  "filter_combo"     — filter combination too restrictive
  "filter_mismatch"  — filter values don't exist in the data
  "bad_join"         — all tables have data but joined result is empty (repair candidate)
  "table_empty"      — a source table itself has no rows
  "aggregate_filter" — HAVING clause removes all results
  "unknown"          — no diagnosis possible

Uses sqlglot to strip WHERE conditions from the full SQL so Stage 3 probes are
always structurally valid even for complex multi-CTE queries.
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


def _get_probe_base(sql: str) -> tuple | None:
    """Parse the generated SQL and return (base_select_expr, aliases_map).

    Finds the SELECT that contains the actual table FROM+JOINs needed for
    Stage 1 and Stage 2 filter probes.  For CTE queries, picks the CTE body
    that has the most real (non-CTE) table JOINs — better than always taking
    the first CTE, which may be a simple filter with no joins.

    aliases_map: {table_fqn: alias_used_in_sql}
    """
    import sqlglot
    import sqlglot.expressions as exp

    if not sql or not sql.strip():
        return None

    try:
        stmt = sqlglot.parse_one(sql, dialect="redshift")
    except Exception:
        return None

    with_clause = stmt.args.get("with")

    if not with_clause or not with_clause.expressions:
        real_select = stmt if isinstance(stmt, exp.Select) else None
    else:
        cte_names: set[str] = {
            cte.alias.lower() for cte in with_clause.expressions if cte.alias
        }

        best_body: exp.Select | None = None
        best_score = -1
        first_valid: exp.Select | None = None

        for cte_expr in with_clause.expressions:
            body = cte_expr.this
            if isinstance(body, exp.Subquery):
                body = body.this
            if not isinstance(body, exp.Select) or not body.args.get("from"):
                continue

            if first_valid is None:
                first_valid = body

            from_src = body.args.get("from")
            joins = body.args.get("joins") or []

            all_srcs = [from_src.this] if from_src else []
            for j in joins:
                all_srcs.append(j.this)

            real_tables = 0
            for src in all_srcs:
                if isinstance(src, exp.Alias):
                    src = src.this
                if isinstance(src, exp.Table) and src.name.lower() not in cte_names:
                    real_tables += 1

            score = real_tables * 10 + len(joins)
            if score > best_score:
                best_score = score
                best_body = body

        real_select = best_body or first_valid

    if not isinstance(real_select, exp.Select):
        return None
    if not real_select.args.get("from"):
        return None

    aliases: dict[str, str] = {}

    def _register(node) -> None:
        if isinstance(node, exp.Alias):
            tbl, alias = node.this, node.alias
        elif isinstance(node, exp.Table):
            tbl, alias = node, node.alias or ""
        else:
            return
        if not isinstance(tbl, exp.Table):
            return
        db = tbl.args.get("db")
        fqn = f"{db.name}.{tbl.name}" if db else tbl.name
        if fqn:
            aliases[fqn] = alias or tbl.name

    _register(real_select.args["from"].this)
    for join in (real_select.args.get("joins") or []):
        _register(join.this)

    return real_select, aliases


def _apply_where(probe, where_clause: str) -> None:
    """Set or clear the WHERE clause on a sqlglot Select expression in place."""
    import sqlglot
    if where_clause:
        try:
            dummy = sqlglot.parse_one(f"SELECT 1 WHERE {where_clause}", dialect="redshift")
            probe.set("where", dummy.args.get("where"))
        except Exception:
            probe.set("where", None)
    else:
        probe.set("where", None)


async def _count_bare_full_sql(sql: str, state: AnalyticsState) -> int:
    """Strip all WHERE/HAVING from every SELECT in the full SQL and count rows.

    For CTE queries: modifies the outermost SELECT directly instead of wrapping
    in a subquery (CTEs inside subqueries are invalid in Redshift).
    Returns -1 on any error.
    """
    from app.services.agents.redshift_client import execute_query
    import sqlglot
    import sqlglot.expressions as exp

    if not sql or not sql.strip():
        return -1
    try:
        stmt = sqlglot.parse_one(sql, dialect="redshift")
        # Strip WHERE/HAVING from all nested SELECTs (inside CTEs)
        for sel in stmt.find_all(exp.Select):
            sel.set("where", None)
            sel.set("having", None)
        # Replace outermost SELECT expressions with COUNT(*) — keep CTEs + FROM intact.
        # Result: WITH cash_agg AS (...), facility_agg AS (...) SELECT COUNT(*) AS cnt FROM joined
        stmt.set("expressions", [exp.alias_(exp.Count(this=exp.Star()), "cnt")])
        stmt.set("group", None)
        stmt.set("order", None)
        stmt.set("limit", None)
        count_sql = stmt.sql(dialect="redshift", pretty=False)
        if not count_sql:
            return -1
        _, rows = await execute_query(count_sql, timeout_s=30, thread_id=state["thread_id"])
        return int(rows[0][0]) if rows and rows[0] else 0
    except Exception as e:
        logger.warning("zero_row_probe | bare_full_sql probe failed | error={}", e)
        return -1


def _extract_all_tables(sql: str) -> list[str]:
    """Return all real (non-CTE) table FQNs referenced anywhere in the SQL."""
    import sqlglot
    import sqlglot.expressions as exp

    if not sql:
        return []
    try:
        stmt = sqlglot.parse_one(sql, dialect="redshift")
        cte_names: set[str] = set()
        with_clause = stmt.args.get("with")
        if with_clause:
            cte_names = {cte.alias.lower() for cte in with_clause.expressions if cte.alias}
        seen: list[str] = []
        for tbl in stmt.find_all(exp.Table):
            if not tbl.name or tbl.name.lower() in cte_names:
                continue
            db = tbl.args.get("db")
            fqn = f"{db.name}.{tbl.name}" if db else tbl.name
            if fqn not in seen:
                seen.append(fqn)
        return seen
    except Exception:
        return []


async def _probe_individual_tables(table_fqns: list[str], state: AnalyticsState) -> dict:
    """Probe each table individually to classify a Stage 3 zero-row result.

    Returns probe_type="table_empty" if a source table itself has no rows, or
    probe_type="bad_join" if all tables have data but the join produces nothing.
    """
    from app.services.agents.redshift_client import execute_query

    for fqn in table_fqns:
        try:
            _, rows = await execute_query(
                f"SELECT COUNT(*) AS cnt FROM {fqn}",
                timeout_s=15,
                thread_id=state["thread_id"],
            )
            count = int(rows[0][0]) if rows and rows[0] else 0
            if count == 0:
                logger.info(
                    "zero_row_probe | stage3b | table {} has 0 rows | thread={}",
                    fqn, state["thread_id"],
                )
                return {
                    "probe_type": "table_empty",
                    "needs_clarification": False,
                    "reason": (
                        f"The source table {fqn} contains no data. "
                        "The table may be empty or the relevant data has not been loaded."
                    ),
                }
        except Exception as e:
            logger.warning(
                "zero_row_probe | stage3b | count failed for {} | error={}", fqn, e
            )

    return {
        "probe_type": "bad_join",
        "needs_clarification": False,
        "reason": (
            "All source tables contain data but the join produces 0 rows. "
            "The join ON clause likely references incorrect column names — "
            "the SQL will be rewritten using the correct join paths."
        ),
    }


# ── Main probe ────────────────────────────────────────────────────────────────

async def zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    if not ir or not ir.anchor_tables:
        logger.warning(
            "zero_row_probe | early exit | no IR or anchor_tables empty | thread={}",
            state.get("thread_id", "?"),
        )
        return {
            "probe_type": "unknown",
            "needs_clarification": False,
            "reason": "No data found for the requested query.",
        }

    from app.services.agents.redshift_client import execute_query
    import sqlglot.expressions as exp

    sql_list = state.get("sql_list") or []
    generated_sql = sql_list[0] if sql_list else ""

    probe_info = _get_probe_base(generated_sql)
    if not probe_info:
        logger.warning(
            "zero_row_probe | early exit | _get_probe_base failed (SQL unparseable or no real tables) | thread={}",
            state.get("thread_id", "?"),
        )
        return {
            "probe_type": "unknown",
            "needs_clarification": False,
            "reason": "No data found matching the query criteria.",
        }

    base_select, aliases = probe_info

    where_filters = [f for f in (ir.filters or []) if not f.is_having and f.resolved]
    having_filters = [f for f in (ir.filters or []) if f.is_having and f.resolved]
    time_filter = ir.time_filter if (ir.time_filter and ir.time_filter.resolved) else None
    sample_limit = min(ir.limit or 5, 5)

    async def _count(where_clause: str) -> int:
        probe = base_select.copy()
        probe.set("expressions", [exp.alias_(exp.Count(this=exp.Star()), "cnt")])
        probe.set("group", None)
        probe.set("having", None)
        probe.set("order", None)
        probe.set("limit", None)
        _apply_where(probe, where_clause)
        sql = probe.sql(dialect="redshift", pretty=False)
        try:
            _, rows = await execute_query(sql, timeout_s=30, thread_id=state["thread_id"])
            return int(rows[0][0]) if rows and rows[0] else 0
        except Exception as e:
            logger.warning("zero_row_probe | count query failed | sql_preview={} | error={}", sql[:200], e)
            return -1

    async def _sample(where_clause: str) -> int:
        import sqlglot
        probe = base_select.copy()
        probe.set("expressions", [exp.Star()])
        probe.set("group", None)
        probe.set("having", None)
        probe.set("order", None)
        limit_expr = sqlglot.parse_one(f"SELECT 1 LIMIT {sample_limit}", dialect="redshift")
        probe.set("limit", limit_expr.args.get("limit"))
        _apply_where(probe, where_clause)
        sql = probe.sql(dialect="redshift", pretty=False)
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
                "probe_type": "time_filter",
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
                        "probe_type": "filter_combo",
                        "needs_clarification": True,
                        "reason": (
                            f"Found {s2_count:,} record(s) when OR-ing all filter conditions, "
                            f"but 0 when AND-ing them. The combination of {desc} is too restrictive — "
                            "each value exists individually but not together. "
                            "Try removing one filter at a time."
                        ),
                    }

    # ── Stage 3: full SQL with all WHERE/HAVING stripped ─────────────────────
    # Uses the complete SQL so complex multi-CTE queries are probed correctly.
    s3_count = await _count_bare_full_sql(generated_sql, state)
    if s3_count == -1:
        s3_count = await _count("")
    logger.info(
        "zero_row_probe | stage3 (bare query, no filters) | count={} | thread={}",
        s3_count, state["thread_id"],
    )

    if s3_count > 0:
        await _sample("")
        if where_filters:
            desc = _describe_filters(where_filters)
            time_msg = " and time filter" if time_filter else ""
            return {
                "probe_type": "filter_mismatch",
                "needs_clarification": True,
                "reason": (
                    f"The joined tables contain {s3_count:,} record(s) but all applied filters"
                    f"{time_msg} ({desc}) return 0 results. "
                    "The filter values may not match what is stored — check exact spelling and casing."
                ),
            }
        elif time_filter:
            return {
                "probe_type": "time_filter",
                "needs_clarification": True,
                "reason": (
                    f"The tables contain {s3_count:,} record(s) but none fall in the requested "
                    "time period. Try a broader date range."
                ),
            }
        return {
            "probe_type": "unknown",
            "needs_clarification": False,
            "reason": "The tables have data but no rows matched your query criteria.",
        }

    if s3_count == 0:
        # ── Stage 3b: probe each real table individually ──────────────────────
        all_tables = _extract_all_tables(generated_sql)
        logger.info(
            "zero_row_probe | stage3b (individual table probes) | tables={} | thread={}",
            all_tables, state["thread_id"],
        )
        if all_tables:
            return await _probe_individual_tables(all_tables, state)
        return {
            "probe_type": "bad_join",
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
            "probe_type": "aggregate_filter",
            "needs_clarification": True,
            "reason": (
                f"Data exists but the aggregate filter {desc} removes all results. "
                "Try relaxing this threshold."
            ),
        }

    return {
        "probe_type": "unknown",
        "needs_clarification": False,
        "reason": "No data found matching all query criteria.",
    }

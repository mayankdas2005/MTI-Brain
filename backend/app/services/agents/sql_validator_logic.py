"""AST-based SQL validator for Redshift queries.

Pure function — no I/O. Returns (is_valid, error_message).
Gates are applied in order; first failure short-circuits.
"""

from __future__ import annotations

from app.core.logger import logger


def validate_sql(sql: str) -> tuple[bool, str]:
    """Run all validation gates on the SQL string.

    Returns (True, "") on success, (False, error_message) on failure.
    """
    try:
        return _run_gates(sql)
    except Exception as e:
        logger.error("sql_validator_logic unexpected error: {}", e)
        return False, f"Validator internal error: {e}"


def _run_gates(sql: str) -> tuple[bool, str]:
    import sqlglot

    # Gate 1 — Statement type (hard reject)
    try:
        statements = sqlglot.parse(sql, dialect="redshift")
    except Exception as e:
        return False, f"SQL parse error: {e}"

    if not statements:
        return False, "Empty SQL — no statements found"

    if len(statements) > 1:
        return False, "Multiple statements detected — only single SELECT allowed"

    stmt = statements[0]
    if not isinstance(stmt, sqlglot.expressions.Select):
        stmt_type = type(stmt).__name__
        logger.warning("sql_validator DDL/DML rejected | stmt_type={}", stmt_type)
        return False, f"DDL/DML rejected: {stmt_type} is not allowed — only SELECT statements"

    # Gate 2 — Identifier safety
    sql_upper = sql.upper()
    for forbidden in ["EXEC ", "EXECUTE ", "DROP ", "DELETE ", "INSERT ", "UPDATE ", "TRUNCATE "]:
        if forbidden in sql_upper:
            return False, f"Forbidden keyword detected: {forbidden.strip()}"

    # Gate 3 — Schema prefix check
    if "lpp." not in sql.lower():
        logger.warning("sql_validator | no lpp. schema prefix found")

    # Gate 3.5 — CTE table reference check (qualified refs to tables not in scope)
    ok, msg = _check_cte_table_refs(stmt)
    if not ok:
        return False, msg

    # Gate 3.6 — CTE column forwarding check (bare column refs not exported by upstream CTE)
    ok, msg = _check_cte_column_forwarding(stmt)
    if not ok:
        return False, msg

    # Gate 4 — Cartesian join detection (JOIN without ON)
    # if _has_cartesian_join(sql):
    #     return False, "JOIN without ON clause detected — cartesian product risk"

    # Gate 5 — Aggregate in WHERE clause
    # if _has_aggregate_in_where(sql):
    #     return False, "Aggregate function in WHERE clause — use HAVING instead"

    return True, ""


def _check_cte_table_refs(parsed) -> tuple[bool, str]:
    """Gate 3.5: catch table qualifiers referenced in a CTE's SELECT/WHERE but not JOINed.

    Handles three-part names (lpp.currency.code → col.table = 'currency'),
    aliases (added to from_tables), and nested subqueries (skipped via ancestor check).
    """
    import sqlglot.expressions as exp

    for cte in parsed.find_all(exp.CTE):
        cte_body = cte.this
        # Unwrap Subquery wrapper present in some sqlglot versions
        if isinstance(cte_body, exp.Subquery):
            cte_body = cte_body.this
        if not isinstance(cte_body, exp.Select):
            continue

        # Collect all tables/aliases that are directly FROM/JOIN of this CTE's select
        # (not from nested subqueries inside it)
        from_tables: set[str] = set()
        for table in cte_body.find_all(exp.Table):
            ancestor = table.find_ancestor(exp.Select)
            if ancestor is not cte_body:
                continue  # belongs to a nested subquery
            if table.name:
                from_tables.add(table.name.lower())
            if table.alias:
                from_tables.add(table.alias.lower())

        cte_name = (cte.alias or "").lower()

        # Check column qualifiers used directly in this CTE's scope
        for col in cte_body.find_all(exp.Column):
            col_scope = col.find_ancestor(exp.Select)
            if col_scope is not cte_body:
                continue  # belongs to a nested subquery
            tbl = (col.table or "").lower()
            if not tbl:
                continue
            if tbl in from_tables or tbl == cte_name:
                continue
            return False, (
                f"CTE '{cte.alias}' uses `{col.table}.{col.name}` but `{col.table}` is not in "
                f"this CTE's FROM clause (tables in scope: {', '.join(sorted(from_tables)) or 'none'}). "
                f"Fix A: add `{col.name}` to the upstream CTE's SELECT list and reference it as "
                f"bare `{col.name}` in this CTE. "
                f"Fix B: move the JOIN to `{col.table}` into this CTE's FROM clause."
            )

    return True, ""


def _check_cte_column_forwarding(parsed) -> tuple[bool, str]:
    """Gate 3.6: catch bare column refs in CTE-only scopes absent from upstream CTE exports.

    Only fires when ALL FROM/JOIN sources of a CTE are other CTEs (no real tables in scope).
    Silently skips when real tables are in scope or an upstream CTE uses SELECT *.
    """
    import sqlglot.expressions as exp

    # Pass 1 — collect exported alias sets for every named CTE
    cte_exports: dict[str, set[str]] = {}
    for cte in parsed.find_all(exp.CTE):
        cte_body = cte.this
        if isinstance(cte_body, exp.Subquery):
            cte_body = cte_body.this
        if not isinstance(cte_body, exp.Select):
            continue

        cte_name = (cte.alias or "").lower()
        exports: set[str] = set()
        for sel_expr in cte_body.expressions:
            if isinstance(sel_expr, exp.Alias):
                exports.add(sel_expr.alias.lower())
            elif isinstance(sel_expr, exp.Column):
                exports.add(sel_expr.name.lower())
            elif isinstance(sel_expr, exp.Star):
                exports.add("*")
        cte_exports[cte_name] = exports

    # Pass 2 — validate column refs in each downstream CTE
    for cte in parsed.find_all(exp.CTE):
        cte_body = cte.this
        if isinstance(cte_body, exp.Subquery):
            cte_body = cte_body.this
        if not isinstance(cte_body, exp.Select):
            continue

        # Identify direct FROM/JOIN sources (not nested subqueries)
        upstream_ctes: set[str] = set()
        has_real_table = False
        for table in cte_body.find_all(exp.Table):
            if table.find_ancestor(exp.Select) is not cte_body:
                continue
            src = (table.name or "").lower()
            if src in cte_exports:
                upstream_ctes.add(src)
            elif src:
                has_real_table = True
                break

        if has_real_table or not upstream_ctes:
            continue  # real tables in scope — column origins unknown; skip

        # Build available column set; skip if any upstream uses SELECT *
        available: set[str] = set()
        star_upstream = False
        for src in upstream_ctes:
            exp_set = cte_exports.get(src, set())
            if "*" in exp_set:
                star_upstream = True
                break
            available.update(exp_set)

        if star_upstream or not available:
            continue

        # Check every column ref in this CTE's direct scope
        for col in cte_body.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is not cte_body:
                continue
            tbl = (col.table or "").lower()
            col_name = (col.name or "").lower()
            if not col_name:
                continue

            if tbl and tbl in cte_exports:
                # Qualified ref to a known upstream CTE — verify export list
                cte_col_exp = cte_exports[tbl]
                if "*" not in cte_col_exp and col_name not in cte_col_exp:
                    return False, (
                        f"CTE '{cte.alias}' references '{col.table}.{col.name}' "
                        f"but CTE '{col.table}' does not export '{col.name}'. "
                        f"Exported by '{col.table}': {', '.join(sorted(cte_col_exp))}. "
                        f"Add '{col.name}' to '{col.table}' SELECT list."
                    )
            elif not tbl:
                # Bare column ref — must be exported by at least one upstream CTE
                if col_name not in available:
                    return False, (
                        f"CTE '{cte.alias}' references bare column '{col.name}' "
                        f"which is not exported by any upstream CTE "
                        f"({', '.join(sorted(upstream_ctes))}). "
                        f"Available columns: {', '.join(sorted(available))}. "
                        f"Add '{col.name}' to the upstream CTE's SELECT, or check for a typo."
                    )

    # Pass 3 — validate the outermost SELECT (final query after all CTEs)
    # Pattern: WITH agg AS (SELECT id, total ...) SELECT id, rate FROM agg  ← 'rate' not in agg
    outer_select = parsed
    if isinstance(outer_select, exp.Select):
        outer_upstream: set[str] = set()
        outer_has_real = False
        for table in outer_select.find_all(exp.Table):
            if table.find_ancestor(exp.Select) is not outer_select:
                continue
            src = (table.name or "").lower()
            if src in cte_exports:
                outer_upstream.add(src)
            elif src:
                outer_has_real = True
                break

        if not outer_has_real and outer_upstream:
            outer_avail: set[str] = set()
            star_outer = False
            for src in outer_upstream:
                exp_set = cte_exports.get(src, set())
                if "*" in exp_set:
                    star_outer = True
                    break
                outer_avail.update(exp_set)

            if not star_outer and outer_avail:
                for col in outer_select.find_all(exp.Column):
                    if col.find_ancestor(exp.Select) is not outer_select:
                        continue
                    tbl = (col.table or "").lower()
                    col_name = (col.name or "").lower()
                    if not col_name or tbl:
                        continue  # qualified refs handled by Gate 3.5
                    if col_name not in outer_avail:
                        return False, (
                            f"Final SELECT references bare column '{col.name}' "
                            f"not exported by CTE(s) in its FROM "
                            f"({', '.join(sorted(outer_upstream))}). "
                            f"Available: {', '.join(sorted(outer_avail))}. "
                            f"Add '{col.name}' to the upstream CTE's SELECT list."
                        )

    return True, ""


def try_fix_cte_refs(sql: str) -> str | None:
    """Strip illegal table qualifiers from downstream CTE column references.

    When a downstream CTE writes lpp.bank_statement_balance.amount but
    bank_statement_balance is not in that CTE's FROM, replace the qualified
    reference with just the bare column name (amount). The upstream CTE already
    selected that column — its name in the result set is the bare column name.

    Returns the fixed SQL string, or None if no fix was applicable.
    """
    import sqlglot
    import sqlglot.expressions as exp

    try:
        statements = sqlglot.parse(sql, dialect="redshift")
        if not statements or len(statements) != 1:
            return None

        stmt = statements[0]
        modified = False

        for cte in stmt.find_all(exp.CTE):
            cte_body = cte.this
            if isinstance(cte_body, exp.Subquery):
                cte_body = cte_body.this
            if not isinstance(cte_body, exp.Select):
                continue

            from_tables: set[str] = set()
            for table in cte_body.find_all(exp.Table):
                ancestor = table.find_ancestor(exp.Select)
                if ancestor is not cte_body:
                    continue
                if table.name:
                    from_tables.add(table.name.lower())
                if table.alias:
                    from_tables.add(table.alias.lower())

            for col in list(cte_body.find_all(exp.Column)):
                col_scope = col.find_ancestor(exp.Select)
                if col_scope is not cte_body:
                    continue
                tbl = (col.table or "").lower()
                if not tbl or tbl in from_tables:
                    continue
                col.replace(exp.Column(this=exp.Identifier(this=col.name, quoted=col.this.quoted)))
                modified = True

        if not modified:
            return None
        return stmt.sql(dialect="redshift")
    except Exception:
        return None


def _has_cartesian_join(sql: str) -> bool:
    """Detect JOIN without ON or USING clause using sqlglot AST (CTE-safe)."""
    import sqlglot
    import sqlglot.expressions as exp
    try:
        for stmt in sqlglot.parse(sql, dialect="redshift"):
            for join in stmt.find_all(exp.Join):
                if not join.args.get("on") and not join.args.get("using"):
                    return True
    except Exception:
        pass
    return False


def _has_aggregate_in_where(sql: str) -> bool:
    """Detect aggregate functions in WHERE clauses using sqlglot AST (CTE-safe).

    Regex-based detection was unreliable because it spanned CTE boundaries,
    picking up SUM() from the aggregated CTE's SELECT while examining the
    base_data CTE's WHERE clause.
    """
    import sqlglot
    import sqlglot.expressions as exp
    try:
        for stmt in sqlglot.parse(sql, dialect="redshift"):
            for where in stmt.find_all(exp.Where):
                if where.find(exp.AggFunc):
                    return True
    except Exception:
        pass
    return False

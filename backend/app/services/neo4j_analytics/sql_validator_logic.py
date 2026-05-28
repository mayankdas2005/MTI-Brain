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

    # Gate 3.5 — CTE table reference check
    ok, msg = _check_cte_table_refs(stmt)
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
                f"CTE '{cte.alias}' references table '{col.table}' which is not in its FROM clause. "
                f"Downstream CTEs must reference column aliases from prior CTEs, not schema.table.column. "
                f"Either move the JOIN into this CTE, or use the alias defined in the upstream CTE."
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

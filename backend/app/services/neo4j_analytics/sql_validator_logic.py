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

    # Check for semicolons that could indicate multiple statements
    if ";" in sql.rstrip(";"):
        return False, "Semicolon found mid-query — possible multiple statements"

    # Gate 3 — Schema prefix check
    if "lpp." not in sql.lower():
        logger.warning("sql_validator | no lpp. schema prefix found")

    # Gate 4 — Cartesian join detection (JOIN without ON)
    if _has_cartesian_join(sql):
        return False, "JOIN without ON clause detected — cartesian product risk"

    # Gate 5 — Aggregate in WHERE clause
    if _has_aggregate_in_where(sql_upper):
        return False, "Aggregate function in WHERE clause — use HAVING instead"

    return True, ""


def _has_cartesian_join(sql: str) -> bool:
    """Detect JOIN without ON or USING clause."""
    import re
    sql_upper = sql.upper()
    join_positions = [m.start() for m in re.finditer(r'\bJOIN\b', sql_upper)]
    for pos in join_positions:
        segment = sql_upper[pos:pos + 200]
        if " ON " not in segment and " USING " not in segment:
            return True
    return False


def _has_aggregate_in_where(sql_upper: str) -> bool:
    """Detect aggregate functions used in WHERE clause."""
    import re
    where_match = re.search(r'\bWHERE\b(.*?)(?:\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', sql_upper, re.DOTALL)
    if not where_match:
        return False
    where_clause = where_match.group(1)
    agg_functions = ["SUM(", "COUNT(", "AVG(", "MAX(", "MIN(", "LISTAGG("]
    return any(agg in where_clause for agg in agg_functions)

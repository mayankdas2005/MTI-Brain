"""SQL analysis helpers for the answer trust strip.

The trust strip on every assistant answer shows the source tables a query
touched. This module owns that extraction — the frontend never parses SQL.

Source: sqlglot (Snowflake dialect). We strip CTE-defined names so we
return only physical tables the query actually reads from.
"""

from __future__ import annotations

from app.core.logger import logger
import sqlglot
from sqlglot import exp


def extract_source_tables(sql: str | None, dialect: str = "snowflake") -> list[str]:
    """Extract physical source tables from a SQL string, primary first.

    "Primary first" means: the deepest, leftmost FROM in the outermost
    SELECT comes first, then siblings in source-order. Tables defined as
    CTEs (`WITH foo AS (...)`) are excluded — only physical reads are
    returned.

    Returns an empty list if the SQL is empty, can't be parsed, or only
    references CTEs.
    """
    if not sql or not sql.strip():
        return []

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        logger.warning(f"sqlglot parse failed for trust strip: {e}")
        return []
    except Exception as e:
        logger.warning(f"sqlglot raised unexpectedly: {e}")
        return []

    if tree is None:
        return []

    # Names defined by CTEs aren't physical tables — exclude them.
    cte_aliases: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            cte_aliases.add(alias.lower())

    seen: set[str] = set()
    ordered: list[str] = []
    for tbl in tree.find_all(exp.Table):
        # Build the FQN from catalog/db/name parts directly. Going through
        # `tbl.sql(dialect=...)` would bleed the `AS alias` clause into the
        # output ("sales.fct_orders AS o") which is wrong for trust-strip
        # display. We want just the physical table identifier.
        name = tbl.name
        if not name:
            continue
        if name.lower() in cte_aliases:
            continue
        parts = [p for p in (tbl.catalog, tbl.db, name) if p]
        fqn = ".".join(parts)
        key = fqn.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(fqn)

    return ordered

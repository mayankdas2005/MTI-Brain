"""AST-based SQL validator for Redshift queries.

Pure function — no I/O. Returns (is_valid, error_message).
Gates are applied in order; first failure short-circuits.
"""

from __future__ import annotations

from app.core.logger import logger


def detect_fan_out_joins(sql: str, fan_out_risk_fqns: set) -> list[str]:
    """Return FQNs that appear as a direct JOIN target despite being flagged fan-out-risk.

    fan_out_risk_fqns is derived from schema_enricher state — not hardcoded.
    Only detects direct JOIN patterns; pre-aggregate CTEs and IN subqueries are fine.
    """
    import re
    sql_lower = sql.lower()
    hits: list[str] = []
    for fqn in (fan_out_risk_fqns or set()):
        parts = fqn.rsplit(".", 1)
        if len(parts) != 2:
            continue
        schema, tbl = parts
        if re.search(rf'\bjoin\s+{re.escape(schema)}\.{re.escape(tbl)}\b', sql_lower):
            hits.append(fqn)
    return hits


def validate_sql(sql: str, fan_out_risk_fqns: set | None = None) -> tuple[bool, str]:
    """Run all validation gates on the SQL string.

    Returns (True, "") on success, (False, error_message) on failure.
    fan_out_risk_fqns: set of FQNs flagged by schema_enricher as high fan-out risk.
    """
    try:
        return _run_gates(sql, fan_out_risk_fqns=fan_out_risk_fqns or set())
    except Exception as e:
        logger.error("sql_validator_logic unexpected error: {}", e)
        return False, f"Validator internal error: {e}"


def _run_gates(sql: str, fan_out_risk_fqns: set | None = None) -> tuple[bool, str]:
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
    # UNION / UNION ALL are read-only set operations on SELECT statements — allow them.
    # sqlglot parses "SELECT ... UNION ALL SELECT ..." as Union, not Select.
    _READ_ONLY_TYPES = (
        sqlglot.expressions.Select,
        sqlglot.expressions.Union,
        sqlglot.expressions.Intersect,
        sqlglot.expressions.Except,
    )
    if not isinstance(stmt, _READ_ONLY_TYPES):
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

    # Gate 3.7 — Ambiguous column in JOIN ON (bare column ref when 2+ tables in scope share the name)
    ok, msg = _check_ambiguous_join_on(stmt)
    if not ok:
        return False, msg

    # Gate 3.8 — Direct JOIN to fan-out-risk table (row multiplication risk)
    # fan_out_risk_fqns is derived from schema_enricher state — not hardcoded.
    # Blocks direct JOIN; pre-aggregate CTEs and IN subqueries pass.
    if fan_out_risk_fqns:
        _fanout_hits = detect_fan_out_joins(sql, fan_out_risk_fqns)
        if _fanout_hits:
            _fqn_list = ", ".join(_fanout_hits)
            return False, (
                f"Direct JOIN to fan-out-risk table(s) detected: {_fqn_list}. "
                "These tables have many rows per join key — direct JOIN multiplies every source row. "
                "FIX: Replace direct JOIN with (a) WHERE key IN (SELECT DISTINCT key FROM table WHERE ...) "
                "or (b) WITH agg AS (SELECT key, AGG(val) FROM table GROUP BY key) JOIN agg ON ..."
            )

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
    # Pattern: WITH agg AS (SELECT id, total ...) SELECT id, rate FROM agg  -- 'rate' not in agg
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


def validate_column_names(sql: str, schema_columns: list[dict]) -> tuple[bool, str]:
    """Gate 5: validate qualified column refs exist in the known schema.

    Only fires when schema_columns is non-empty — degrades gracefully to (True, "")
    when no schema info is available. Skips CTE-scoped refs (handled by gates 3.5/3.6).
    Conservative: skips qualifiers that cannot be resolved to a known table.
    """
    if not schema_columns:
        return True, ""

    import sqlglot
    import sqlglot.expressions as exp

    # Build lookup: short_table_name_lower → {col_name_lower, ...}
    #          and  full_table_fqn_lower   → {col_name_lower, ...}
    schema_lookup: dict[str, set[str]] = {}
    for col in schema_columns:
        fqn = (col.get("table_fqn") or "").lower()
        name = (col.get("name") or "").lower()
        if not fqn or not name:
            continue
        short = fqn.rsplit(".", 1)[-1]          # "counterparty_exposure"
        schema_lookup.setdefault(fqn, set()).add(name)
        schema_lookup.setdefault(short, set()).add(name)

    if not schema_lookup:
        return True, ""

    try:
        statements = sqlglot.parse(sql, dialect="redshift")
    except Exception:
        return True, ""   # Gate 1 handles parse failures

    if not statements:
        return True, ""

    stmt = statements[0]
    cte_names: set[str] = {(cte.alias or "").lower() for cte in stmt.find_all(exp.CTE)}
    errors: list[str] = []

    for select in stmt.find_all(exp.Select):
        # Build alias → short_table_name for this SELECT scope only
        alias_map: dict[str, str] = {}
        for table in select.find_all(exp.Table):
            if table.find_ancestor(exp.Select) is not select:
                continue
            short = (table.name or "").lower()
            if not short:
                continue
            if table.alias:
                alias_map[table.alias.lower()] = short
            alias_map[short] = short
            if table.db:
                alias_map[f"{table.db.lower()}.{short}"] = short

        for col in select.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is not select:
                continue
            qualifier = (col.table or "").lower()
            col_name = (col.name or "").lower()
            if not qualifier or not col_name:
                continue
            if qualifier in cte_names:
                continue   # CTE-scoped, handled by gates 3.5/3.6

            resolved = alias_map.get(qualifier)
            if resolved is None or resolved in cte_names:
                continue   # unknown qualifier — be conservative, skip
            if resolved not in schema_lookup:
                continue   # table not in our schema (subquery alias, etc.)

            available = schema_lookup[resolved]
            if col_name not in available:
                sample = ", ".join(sorted(available)[:12])
                suffix = " ..." if len(available) > 12 else ""
                errors.append(
                    f"column '{col.table}.{col.name}' does not exist in '{resolved}'; "
                    f"available: {sample}{suffix}"
                )
                if len(errors) >= 3:
                    break
        if len(errors) >= 3:
            break

    if errors:
        return False, "Schema validation: " + " | ".join(errors)
    return True, ""


def validate_filter_types(sql: str, schema_columns: list[dict]) -> tuple[bool, str]:
    """Gate 6: detect filter value type mismatches against column data types.

    Currently catches boolean columns compared against non-boolean string literals,
    e.g. `includes_actual = 'Includes Actual'` where includes_actual is boolean.

    Returns (True, "") when valid or schema is empty/unparseable.
    Returns (False, error_message) when a type mismatch is detected.
    Conservative: only fires when the column is unambiguously boolean in the schema.
    """
    if not schema_columns:
        return True, ""

    bool_col_names: set[str] = set()
    bool_cols_by_table: dict[str, set[str]] = {}

    for c in schema_columns:
        fqn = (c.get("table_fqn") or "").lower()
        name = (c.get("name") or "").lower()
        dtype = (c.get("data_type") or "").lower()
        if not name or "bool" not in dtype:
            continue
        bool_col_names.add(name)
        short = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
        bool_cols_by_table.setdefault(short, set()).add(name)
        bool_cols_by_table.setdefault(fqn, set()).add(name)

    if not bool_col_names:
        return True, ""

    import sqlglot
    import sqlglot.expressions as exp

    _VALID_BOOL_STRINGS = {"true", "false", "1", "0", "t", "f", "yes", "no"}

    try:
        for stmt in sqlglot.parse(sql, dialect="redshift"):
            if stmt is None:
                continue

            # Build alias → short table name map across all SELECT scopes
            alias_map: dict[str, str] = {}
            for select in stmt.find_all(exp.Select):
                for table in select.find_all(exp.Table):
                    if table.find_ancestor(exp.Select) is not select:
                        continue
                    short = (table.name or "").lower()
                    if not short:
                        continue
                    if table.alias:
                        alias_map[table.alias.lower()] = short
                    alias_map[short] = short

            for eq in stmt.find_all(exp.EQ):
                left, right = eq.this, eq.expression
                # Normalise: put column on left, literal on right
                if isinstance(left, exp.Literal) and isinstance(right, exp.Column):
                    left, right = right, left
                if not isinstance(left, exp.Column):
                    continue
                if not isinstance(right, exp.Literal) or not right.is_string:
                    continue

                col_name = (left.name or "").lower()
                qualifier = (left.table or "").lower()

                is_bool = False
                if qualifier:
                    resolved = alias_map.get(qualifier, qualifier)
                    if col_name in bool_cols_by_table.get(resolved, set()):
                        is_bool = True
                elif col_name in bool_col_names:
                    is_bool = True

                if is_bool:
                    val = right.this
                    if val.lower() not in _VALID_BOOL_STRINGS:
                        return False, (
                            f"Schema validation: column '{col_name}' is boolean — "
                            f"use TRUE or FALSE instead of string literal '{val}'"
                        )
    except Exception:
        pass

    return True, ""


def _check_ambiguous_join_on(parsed) -> tuple[bool, str]:
    """Gate 3.7: detect bare (unqualified) column references in JOIN ON expressions.

    If two or more tables/subquery aliases in the same SELECT scope both export
    a column with the same name, a bare reference in the ON clause is ambiguous
    and Redshift will raise error 42702.  We collect the aliases visible in each
    SELECT block's FROM/JOIN clause, then check every ON expression for Column
    nodes that have no table qualifier — flagging any name that appears in 2+
    of those aliases' exposed columns.

    Because we don't have the real schema here we use a conservative proxy:
    flag any bare column in an ON clause that appears at least twice as a bare
    column across the entire ON expression set of the same SELECT block.
    """
    import sqlglot.expressions as exp

    try:
        for select in parsed.find_all(exp.Select):
            # Collect every JOIN in this SELECT (not nested)
            joins = [
                j for j in select.args.get("joins", [])
                if isinstance(j, exp.Join)
            ]
            if not joins:
                continue

            # Gather all ON-clause bare column names across all JOINs in this SELECT
            bare_by_join: list[list[str]] = []
            for join in joins:
                on_expr = join.args.get("on")
                if not on_expr:
                    continue
                bare_cols = [
                    col.name.lower()
                    for col in on_expr.find_all(exp.Column)
                    if not col.table  # no table qualifier
                ]
                bare_by_join.append(bare_cols)

            # Count how many different JOINs expose each bare column name
            from collections import Counter
            col_join_count: Counter = Counter()
            for bare_cols in bare_by_join:
                for name in set(bare_cols):
                    col_join_count[name] += 1

            # A bare column that appears in ON clauses across 2+ JOINs in the same
            # SELECT is unambiguously ambiguous — two tables both produce that name.
            for col_name, count in col_join_count.items():
                if count >= 2:
                    return (
                        False,
                        f"Ambiguous column reference '{col_name}' in JOIN ON clause — "
                        f"appears unqualified in {count} JOIN conditions within the same SELECT scope. "
                        f"Qualify both sides: e.g. alias1.{col_name} = alias2.{col_name} "
                        f"(Redshift error 42702).",
                    )
    except Exception:
        pass

    return True, ""


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

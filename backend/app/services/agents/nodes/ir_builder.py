"""SemanticIR construction for the query_compiler node.

Builds the SemanticIR from resolved_intent, loading join paths, and normalizing
filter values. Column validation (fuzzy Redshift check) is handled by ir_utils
after build_semantic_ir returns — not here.
Aggregation (SUM/AVG/COUNT) is left null when not set by the intent resolver —
the SQL LLM decides from the question context and column name.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.semantic_ir import ColumnRef, FilterSpec, SemanticIR


def _normalize_fqn(fqn: str) -> str:
    """Trim 3-part FQNs to schema.table.

    The intent resolver occasionally leaks a column name into the table FQN
    (e.g. 'lpp.bank.description1' instead of 'lpp.bank').  Valid table FQNs
    are always exactly 2 parts — anything longer is a hallucination artifact.
    """
    parts = fqn.split(".")
    if len(parts) > 2:
        normalized = f"{parts[0]}.{parts[1]}"
        logger.warning("ir_builder | 3-part FQN normalized | {} → {}", fqn, normalized)
        return normalized
    return fqn


def _normalize_resolved_fqns(resolved: dict) -> dict:
    """Apply _normalize_fqn to every table_fqn in the resolved intent dict."""
    changes: dict = {}

    if resolved.get("anchor_tables"):
        changes["anchor_tables"] = [_normalize_fqn(t) for t in resolved["anchor_tables"]]

    for key in ("measures", "dimensions", "filters"):
        items = resolved.get(key)
        if items:
            changes[key] = [
                {**item, "table_fqn": _normalize_fqn(item["table_fqn"])}
                if isinstance(item, dict) and item.get("table_fqn") else item
                for item in items
            ]

    if resolved.get("timeframe") and isinstance(resolved.get("timeframe"), dict):
        tf = resolved["timeframe"]
        if tf.get("table_fqn"):
            changes["timeframe"] = {**tf, "table_fqn": _normalize_fqn(tf["table_fqn"])}

    return {**resolved, **changes} if changes else resolved


def build_semantic_ir(resolved: dict, semantic_context: dict) -> SemanticIR:
    resolved = _normalize_resolved_fqns(resolved)
    anchor_tables = list(resolved.get("anchor_tables") or _extract_anchor_tables(resolved))

    ref_tables = _extract_anchor_tables(resolved)
    missing = [t for t in ref_tables if t not in anchor_tables]
    if missing:
        anchor_tables = anchor_tables + missing
        logger.info("ir_builder | anchor_tables extended | added={} | final={}", missing, anchor_tables)

    join_path_ids, join_clauses, path_tables, join_types, candidate_join_paths = _load_join_paths(anchor_tables)

    raw_measures = resolved.get("measures", [])
    raw_dimensions = resolved.get("dimensions", [])
    measures = [ColumnRef(**m) for m in raw_measures if isinstance(m, dict) and "table_fqn" in m]
    dimensions = [ColumnRef(**d) for d in raw_dimensions if isinstance(d, dict) and "table_fqn" in d]

    measures = _enrich_aggregations(measures, semantic_context)

    filters = _build_filter_specs(resolved.get("filters", []), raw_measures, semantic_context)
    time_filter = _build_time_filter(resolved.get("timeframe"), anchor_tables, semantic_context)

    template_id = resolved.get("template_id", "")
    cte_steps = _get_cte_steps(template_id, semantic_context)

    ir = SemanticIR(
        template_id=template_id,
        intent=resolved.get("intent", ""),
        complexity=resolved.get("complexity", "simple"),
        result_shape=resolved.get("result_shape"),
        anchor_tables=anchor_tables,
        join_path_ids=join_path_ids,
        join_clauses=join_clauses,
        path_tables=path_tables,
        join_types=join_types,
        measures=measures,
        dimensions=dimensions,
        filters=filters,
        time_filter=time_filter,
        temporal_grain=resolved.get("temporal_grain"),
        cte_steps=cte_steps,
        order_by=_coerce_list(resolved.get("order_by")),
        limit=resolved.get("limit"),
        sub_query_index=None,
        candidate_join_paths=candidate_join_paths or None,
    )
    logger.info(
        "ir_builder | ir_built | template={} | anchor_tables={} | measures={} | time_filter={}.{} | filters={}",
        template_id,
        anchor_tables,
        [(m.column_name, m.aggregation) for m in measures],
        time_filter.table_fqn if time_filter else None,
        time_filter.column_name if time_filter else None,
        [(f.column_name, f.operator, str(f.value)[:20], f.is_having) for f in filters],
    )
    return ir


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return list(value)


def _extract_anchor_tables(resolved: dict) -> list[str]:
    tables: set[str] = set()
    for m in resolved.get("measures", []):
        if m.get("table_fqn"):
            tables.add(m["table_fqn"])
    for d in resolved.get("dimensions", []):
        if d.get("table_fqn"):
            tables.add(d["table_fqn"])
    for f in resolved.get("filters", []):
        if f.get("table_fqn"):
            tables.add(f["table_fqn"])
    return list(tables)


import re as _re
_JOIN_COL_RE = _re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)")


def _pick_valid_primary_path(paths: list[dict]) -> dict | None:
    """Return the first path whose join columns are confirmed valid in the Redis schema cache.

    Only checks Redis (synchronous) — no Redshift round-trips. Paths for tables whose
    schema isn't cached yet are treated as valid (can't prove otherwise).
    Falls back to the first path if none pass or all are uncacheable.
    """
    from app.services.agents.redis_client import get_schema_cols

    def _cols_valid(path: dict) -> bool:
        for clause in (path.get("join_clauses") or []):
            for m in _JOIN_COL_RE.finditer(clause):
                schema, table, col = m.group(1), m.group(2), m.group(3)
                cached = get_schema_cols(schema, table)
                if cached is None:
                    continue  # not in cache — can't validate, assume ok
                real_cols = {r[0] for r in cached}
                if col not in real_cols:
                    logger.info(
                        "ir_builder | path_skip | tier={} | col {}.{}.{} not in Redshift schema",
                        path.get("tier"), schema, table, col,
                    )
                    return False
        return True

    # Try each path in order; return first confirmed-valid one
    for path in paths:
        if _cols_valid(path):
            return path

    # All paths failed validation (or no paths) — return first as fallback
    return next(iter(paths), None)


def _load_join_paths(anchor_tables: list[str]) -> tuple[list, list, list, list, list]:
    """Load join paths for consecutive table pairs.

    Collects ALL available paths via collect_all_join_paths (JOINS_TO + dijkstra k1-3
    forward/reverse + yens k1-3 forward/reverse). Primary join_clauses are set from the
    first-priority path (JOINS_TO → dijkstra k=1 → yens k=1) for backward compat.
    All paths are stored in candidate_join_paths for the SQL generator to choose from.
    """
    if len(anchor_tables) <= 1:
        return [], [], list(anchor_tables), [], []

    join_path_ids: list[str] = []
    all_join_clauses: list[str] = []
    all_path_tables: list[str] = [anchor_tables[0]]
    join_types: list[str] = []
    candidate_join_paths: list[dict] = []

    for i in range(len(anchor_tables) - 1):
        from_table = anchor_tables[i]
        to_table = anchor_tables[i + 1]

        all_paths = neo4j_client.collect_all_join_paths(from_table, to_table)

        # Tag each path with from/to for SQL generator context
        for p in all_paths:
            p.setdefault("from_fqn", from_table)
            p.setdefault("to_fqn", to_table)
        candidate_join_paths.extend(all_paths)

        # Primary path: first path whose join columns are confirmed valid in Redis cache.
        # Redis is already populated by context_fetcher for semantic_context tables.
        # If no path passes sync validation (e.g. cache miss), fall back to first path.
        jp = _pick_valid_primary_path(all_paths)
        if not jp:
            logger.warning(
                "ir_builder | NO join found | from={} to={} | sentinel added",
                from_table, to_table,
            )
            all_join_clauses.append("")
            all_path_tables.append(to_table)
            join_types.append("JOIN")
            continue

        logger.info(
            "ir_builder | join resolved | from={} to={} | primary_tier={} | hops={} | total_paths={} | clauses={}",
            from_table, to_table,
            jp.get("tier", "unknown"),
            jp.get("hop_count"),
            len(all_paths),
            jp.get("join_clauses"),
        )

        join_path_ids.append(jp.get("id", ""))
        clauses = jp.get("join_clauses", [])
        path_tbls = jp.get("path_tables", [from_table, to_table])

        for j, clause in enumerate(clauses):
            left_tbl = path_tbls[j] if j < len(path_tbls) else from_table
            right_tbl = path_tbls[j + 1] if j + 1 < len(path_tbls) else to_table
            all_join_clauses.append(_qualify_join_clause(clause, left_tbl, right_tbl))
            join_types.append("JOIN")

        for tbl in path_tbls[1:]:
            if tbl not in all_path_tables:
                all_path_tables.append(tbl)

    return join_path_ids, all_join_clauses, all_path_tables, join_types, candidate_join_paths


def _qualify_join_clause(clause: str, left_table: str, right_table: str) -> str:
    if not clause or "." in clause:
        return clause
    if "=" not in clause:
        return clause
    left_col, right_col = [x.strip() for x in clause.split("=", 1)]
    return f"{left_table}.{left_col} = {right_table}.{right_col}"



def _enrich_aggregations(measures: list[ColumnRef], semantic_context: dict) -> list[ColumnRef]:
    return measures


def _resolve_filter_values(
    column: str,
    table_fqn: str,
    raw_values: list[str],
    operator: str,
    semantic_context: dict,
) -> tuple[str, list[str]]:
    """Normalize filter values against Redshift distinct values (filter_values).

    Returns (operator, resolved_values).
    - ALL exact case-insensitive matches → ("IN", [...]) or ("=", [...]) for single
    - ANY partial match → ("ILIKE_MULTI", ["%val1%", "%val2%", ...])
    - No filter_values available → original operator + raw_values unchanged
    """
    cols = semantic_context.get("columns") or []
    col_meta = next(
        (c for c in cols if c.get("table_fqn") == table_fqn and c.get("name") == column),
        None,
    )
    if not col_meta:
        return operator, raw_values

    filter_values = col_meta.get("filter_values") or []
    if not filter_values:
        return operator, raw_values

    resolved, modes = [], []
    for raw in raw_values:
        raw_lower = str(raw).lower().strip()

        exact = next((fv for fv in filter_values if str(fv).lower() == raw_lower), None)
        if exact:
            logger.info("ir_builder | filter exact | {}.{} | {} → {}", table_fqn, column, raw, exact)
            resolved.append(str(exact))
            modes.append("exact")
            continue

        partials = [fv for fv in filter_values if raw_lower in str(fv).lower()]
        if partials:
            logger.info(
                "ir_builder | filter partial (ILIKE) | {}.{} | {} → candidates={}",
                table_fqn, column, raw, partials[:3],
            )
            resolved.append(f"%{raw}%")
            modes.append("partial")
            continue

        logger.warning(
            "ir_builder | filter value NOT in Redshift distinct values | {}.{} | value={} | sample={}",
            table_fqn, column, raw, filter_values[:5],
        )
        resolved.append(raw)
        modes.append("unknown")

    if all(m == "exact" for m in modes):
        op = "=" if len(resolved) == 1 else "IN"
    else:
        op = "ILIKE_MULTI"

    return op, resolved


def _build_filter_specs(
    raw_filters: list[dict],
    raw_measures: list[dict] | None,
    semantic_context: dict,
) -> list[FilterSpec]:
    _COMPARISON_OPS = {">", ">=", "<", "<=", "!="}

    agg_measure_cols: set[tuple[str, str]] = set()
    for m in (raw_measures or []):
        if isinstance(m, dict) and m.get("aggregation") and m["aggregation"].upper() not in ("", "NONE"):
            agg_measure_cols.add((m.get("table_fqn", ""), m.get("column_name", "")))

    filters: list[FilterSpec] = []
    for f in raw_filters:
        # Accept both "column_name" (new prompt output) and "column" (legacy fallback)
        col_name = f.get("column_name") or f.get("column")
        if not f.get("table_fqn") or not col_name:
            continue
        raw_op = (f.get("operator") or "=").strip()
        raw_value = f.get("raw_value", "")
        is_comparison = raw_op in _COMPARISON_OPS
        is_having = (f["table_fqn"], col_name) in agg_measure_cols

        already_a_pattern = raw_op in ("LIKE", "ILIKE") and isinstance(raw_value, str) and "%" in raw_value
        if not is_comparison and not already_a_pattern and raw_op in ("=", "IN", "LIKE", "ILIKE") and isinstance(raw_value, str):
            raw_list = [raw_value]
            norm_op, norm_values = _resolve_filter_values(
                col_name, f["table_fqn"], raw_list, raw_op, semantic_context,
            )
            if norm_op == "ILIKE_MULTI":
                for v in norm_values:
                    filters.append(FilterSpec(
                        table_fqn=f["table_fqn"],
                        column_name=col_name,
                        operator="ILIKE",
                        value=v,
                        raw_user_value=raw_value,
                        resolved=True,
                        is_having=is_having,
                    ))
                continue
            else:
                final_op = norm_op
                final_value: str | list[str] = norm_values[0] if len(norm_values) == 1 else norm_values
        else:
            final_op = raw_op
            final_value = raw_value

        filters.append(FilterSpec(
            table_fqn=f["table_fqn"],
            column_name=col_name,
            operator=final_op,
            value=final_value,
            raw_user_value=raw_value,
            resolved=is_comparison or already_a_pattern,
            is_having=is_having,
        ))
    return filters


def _build_time_filter(timeframe: str | None, anchor_tables: list[str], semantic_context: dict) -> FilterSpec | None:
    if not timeframe:
        return None
    table_fqn, date_col = _find_date_column(anchor_tables, semantic_context)
    from app.services.agents.filter_resolver_logic import resolve_tier3_temporal
    result = resolve_tier3_temporal(timeframe)
    if not result:
        return FilterSpec(
            table_fqn=table_fqn,
            column_name=date_col,
            operator="=",
            value=timeframe,
            raw_user_value=timeframe,
            resolved=False,
        )
    return FilterSpec(
        table_fqn=table_fqn,
        column_name=date_col,
        operator=result["operator"],
        value=result["value"],
        raw_user_value=timeframe,
        resolved=True,
        is_raw_sql=result.get("is_raw_sql", False),
    )


def _find_date_column(anchor_tables: list[str], semantic_context: dict) -> tuple[str, str]:
    columns = semantic_context.get("columns", [])
    _DATE_TYPES = {"date", "timestamp", "datetime"}
    _DATE_SEMANTICS = {"date", "datetime", "timestamp"}

    for table in anchor_tables:
        for col in columns:
            if col.get("table_fqn") != table:
                continue
            name = col["name"]
            if col.get("temporal_grain"):
                logger.info("ir_builder | date_col | table={} col={} via=temporal_grain", table, name)
                return table, name
            if col.get("semantic_type", "").lower() in _DATE_SEMANTICS:
                logger.info("ir_builder | date_col | table={} col={} via=semantic_type({})", table, name, col.get("semantic_type"))
                return table, name
            if col.get("data_type", "").lower() in _DATE_TYPES:
                logger.info("ir_builder | date_col | table={} col={} via=data_type({})", table, name, col.get("data_type"))
                return table, name

    fallback_table = anchor_tables[0] if anchor_tables else ""
    logger.warning("ir_builder | date_col not found | anchor_tables={} | fallback={}.transaction_date", anchor_tables, fallback_table)
    return fallback_table, "transaction_date"


def _get_cte_steps(template_id: str, semantic_context: dict) -> list[str]:
    return []

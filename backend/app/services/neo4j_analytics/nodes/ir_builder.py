"""SemanticIR construction for the query_compiler node.

Builds the SemanticIR from resolved_intent, loading join paths, validating
column refs, inferring aggregations, and normalizing filter values.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.semantic_ir import ColumnRef, FilterSpec, SemanticIR


def build_semantic_ir(resolved: dict, semantic_context: dict) -> SemanticIR:
    anchor_tables = list(resolved.get("anchor_tables") or _extract_anchor_tables(resolved))

    ref_tables = _extract_anchor_tables(resolved)
    missing = [t for t in ref_tables if t not in anchor_tables]
    if missing:
        anchor_tables = anchor_tables + missing
        logger.info("ir_builder | anchor_tables extended | added={} | final={}", missing, anchor_tables)

    join_path_ids, join_clauses, path_tables, join_types = _load_join_paths(anchor_tables)

    raw_measures = resolved.get("measures", [])
    raw_dimensions = resolved.get("dimensions", [])
    measures = [ColumnRef(**m) for m in raw_measures if isinstance(m, dict) and "table_fqn" in m]
    dimensions = [ColumnRef(**d) for d in raw_dimensions if isinstance(d, dict) and "table_fqn" in d]

    measures, dimensions = _validate_column_refs(measures, dimensions, semantic_context)
    measures = _enrich_aggregations(measures, semantic_context)

    filters = _build_filter_specs(resolved.get("filters", []), raw_measures, semantic_context)
    time_filter = _build_time_filter(resolved.get("timeframe"), anchor_tables, semantic_context)

    template_id = resolved.get("template_id", "")
    cte_steps = _get_cte_steps(template_id, semantic_context)

    ir = SemanticIR(
        template_id=template_id,
        intent=resolved.get("intent", ""),
        complexity=resolved.get("complexity", "simple"),
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


def _load_join_paths(anchor_tables: list[str]) -> tuple[list, list, list, list]:
    """Load join paths for consecutive table pairs.

    Calls load_best_join_path() which tries: JOINS_TO → dijkstra → yens.
    Logs which tier resolved each pair so a junior dev can trace the cascade.
    """
    if len(anchor_tables) <= 1:
        return [], [], list(anchor_tables), []

    join_path_ids: list[str] = []
    all_join_clauses: list[str] = []
    all_path_tables: list[str] = [anchor_tables[0]]
    join_types: list[str] = []

    for i in range(len(anchor_tables) - 1):
        from_table = anchor_tables[i]
        to_table = anchor_tables[i + 1]

        jp = neo4j_client.load_best_join_path(from_table, to_table)
        if not jp:
            logger.warning(
                "ir_builder | NO join found (tried JOINS_TO + JoinPath dijkstra + yens) | from={} to={} | sentinel added",
                from_table, to_table,
            )
            all_join_clauses.append("")
            all_path_tables.append(to_table)
            join_types.append("JOIN")
            continue

        logger.info(
            "ir_builder | join resolved | from={} to={} | tier={} | hops={} | tables={} | clauses={}",
            from_table, to_table,
            jp.get("tier", "unknown"),
            jp.get("hop_count"),
            jp.get("path_tables"),
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

    return join_path_ids, all_join_clauses, all_path_tables, join_types


def _qualify_join_clause(clause: str, left_table: str, right_table: str) -> str:
    if not clause or "." in clause:
        return clause
    if "=" not in clause:
        return clause
    left_col, right_col = [x.strip() for x in clause.split("=", 1)]
    return f"{left_table}.{left_col} = {right_table}.{right_col}"


def _validate_column_refs(
    measures: list[ColumnRef],
    dimensions: list[ColumnRef],
    semantic_context: dict,
) -> tuple[list[ColumnRef], list[ColumnRef]]:
    known = {
        (c.get("table_fqn"), c.get("name"))
        for c in (semantic_context.get("columns") or [])
        if c.get("table_fqn") and c.get("name")
    }
    if not known:
        return measures, dimensions

    valid_measures, valid_dims = [], []
    for m in measures:
        if (m.table_fqn, m.column_name) in known:
            valid_measures.append(m)
        else:
            logger.warning("ir_builder | DROPPED measure (not in schema) | {}.{}", m.table_fqn, m.column_name)
    for d in dimensions:
        if (d.table_fqn, d.column_name) in known:
            valid_dims.append(d)
        else:
            logger.warning("ir_builder | DROPPED dimension (not in schema) | {}.{}", d.table_fqn, d.column_name)
    return valid_measures, valid_dims


def _infer_aggregation(data_type: str) -> str | None:
    dtype = (data_type or "").lower()
    if any(t in dtype for t in ("int", "float", "decimal", "numeric", "double", "real")):
        return "SUM"
    if any(t in dtype for t in ("char", "varchar", "text", "bpchar")):
        return "COUNT"
    return None


def _enrich_aggregations(measures: list[ColumnRef], semantic_context: dict) -> list[ColumnRef]:
    cols_by_key = {
        (c.get("table_fqn"), c.get("name")): c
        for c in (semantic_context.get("columns") or [])
    }
    enriched = []
    for m in measures:
        if not m.aggregation or m.aggregation.upper() in ("", "NONE"):
            meta = cols_by_key.get((m.table_fqn, m.column_name))
            if meta and meta.get("data_type"):
                inferred = _infer_aggregation(meta["data_type"])
                if inferred:
                    m = m.model_copy(update={"aggregation": inferred})
                    logger.info(
                        "ir_builder | aggregation inferred | {}.{} → {} (data_type={})",
                        m.table_fqn, m.column_name, inferred, meta["data_type"],
                    )
        enriched.append(m)
    return enriched


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
    from app.services.neo4j_analytics.filter_resolver_logic import resolve_tier3_temporal
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

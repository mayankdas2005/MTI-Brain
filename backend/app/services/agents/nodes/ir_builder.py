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
from app.services.agents.context.column_loader import get_filter_values as _get_filter_values
from app.services.agents.semantic_ir import ColumnRef, FilterSpec, SemanticIR


_TIMEFRAME_GRAIN: dict[str, str] = {
    "last_7_days":    "day",
    "last_14_days":   "day",
    "last_30_days":   "day",
    "last_week":      "week",
    "last_2_weeks":   "week",
    "last_month":     "month",
    "last_3_months":  "month",
    "last_6_months":  "month",
    "last_12_months": "month",
    "last_year":      "month",
    "this_year":      "month",
    "ytd":            "month",
    "last_quarter":   "quarter",
    "this_quarter":   "quarter",
}


def _infer_temporal_grain(timeframe: str | None) -> str | None:
    if not timeframe:
        return None
    return _TIMEFRAME_GRAIN.get(timeframe.lower().strip().replace(" ", "_"))


def _resolve_boolean_value(raw_value: str) -> str:
    """Map any human-readable label to SQL TRUE or FALSE for boolean columns."""
    raw_lower = raw_value.strip().lower()
    if raw_lower in ("true", "1", "yes", "t", "y", "on", "active", "enabled", "include", "includes"):
        return "TRUE"
    if raw_lower in ("false", "0", "no", "f", "n", "off", "inactive", "disabled", "exclude", "excludes"):
        return "FALSE"
    NEG = frozenset({"not", "no", "none", "false", "exclude", "excludes", "excluded",
                     "without", "missing", "absent", "off", "inactive", "disabled", "non"})
    if set(raw_lower.split()) & NEG:
        return "FALSE"
    return "TRUE"


def _normalize_numeric(raw: str) -> str | None:
    """Strip currency/formatting chars; return cleaned numeric string or None if not a number."""
    import re
    cleaned = re.sub(r"[$,\s]", "", str(raw).strip())
    try:
        float(cleaned)
        return cleaned
    except (ValueError, TypeError):
        return None


def _type_aware_filter_spec(
    f: dict,
    col_name: str,
    raw_value: str,
    raw_op: str,
    semantic_context: dict,
    is_having: bool,
) -> "FilterSpec | None":
    """Fast-path type dispatch for known column types.

    Returns a FilterSpec for types we can resolve deterministically (boolean, numeric).
    Returns None to fall through to the existing vocab-matching logic for all other types.
    """
    cols = semantic_context.get("columns") or []
    col_meta = next(
        (c for c in cols if c.get("table_fqn") == f["table_fqn"] and c.get("name") == col_name),
        None,
    )
    if not col_meta:
        return None

    data_type = (col_meta.get("data_type") or "").lower().strip()

    # Boolean: deterministic TRUE/FALSE — bypass vocab matching entirely
    if "bool" in data_type:
        bool_val = _resolve_boolean_value(raw_value)
        logger.info("ir_builder | bool_filter | {}.{} | '{}' → {}", f["table_fqn"], col_name, raw_value, bool_val)
        return FilterSpec(
            table_fqn=f["table_fqn"],
            column_name=col_name,
            operator="=" if raw_op not in ("!=",) else "!=",
            value=bool_val,
            raw_user_value=raw_value,
            resolved=True,
            is_raw_sql=True,
            is_having=is_having,
        )

    # Date/timestamp: skip here — filter_resolver temporal resolver handles these
    if any(t in data_type for t in ("date", "timestamp", "datetime")):
        return None

    # Integer/numeric types: strip formatting chars, validate
    _INT_TYPES = ("integer", "bigint", "smallint", "int2", "int4", "int8")
    _DEC_TYPES = ("numeric", "decimal", "float", "double", "real")
    is_numeric = (
        data_type in _INT_TYPES
        or data_type == "int"
        or any(t in data_type for t in _DEC_TYPES)
    )
    if is_numeric:
        clean = _normalize_numeric(raw_value)
        if clean:
            logger.info("ir_builder | numeric_filter | {}.{} | '{}' → {}", f["table_fqn"], col_name, raw_value, clean)
            return FilterSpec(
                table_fqn=f["table_fqn"],
                column_name=col_name,
                operator=raw_op if raw_op in (">", ">=", "<", "<=", "!=", "=") else "=",
                value=clean,
                raw_user_value=raw_value,
                resolved=True,
                is_raw_sql=True,
                is_having=is_having,
            )

    return None


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


async def build_semantic_ir(resolved: dict, semantic_context: dict, state: dict | None = None) -> SemanticIR:
    resolved = _normalize_resolved_fqns(resolved)
    anchor_tables = list(resolved.get("anchor_tables") or _extract_anchor_tables(resolved))

    # Extend anchor_tables from tables referenced in measures/dimensions/filters
    ref_tables = _extract_anchor_tables(resolved)
    missing = [t for t in ref_tables if t not in anchor_tables]
    if missing:
        anchor_tables = anchor_tables + missing
        logger.info("ir_builder | anchor_tables extended | added={} | final={}", missing, anchor_tables)

    # Inject cross-domain hub table into anchor_tables (must be at front for joins)
    hub_info = (semantic_context or {}).get("cross_domain_hub") or {}
    hub_fqn = hub_info.get("hub_table_fqn")
    if hub_fqn and hub_fqn not in anchor_tables:
        anchor_tables.insert(0, hub_fqn)
        logger.info("ir_builder | hub_injected | fqn={}", hub_fqn)

    join_path_ids, join_clauses, path_tables, join_types, candidate_join_paths, unresolved_pairs = \
        _load_join_paths(anchor_tables, intent_directive=(state or {}).get("intent_directive") or "")

    raw_measures = resolved.get("measures", [])
    raw_dimensions = resolved.get("dimensions", [])
    measures = [ColumnRef(**m) for m in raw_measures if isinstance(m, dict) and "table_fqn" in m]
    dimensions = [ColumnRef(**d) for d in raw_dimensions if isinstance(d, dict) and "table_fqn" in d]

    measures = _enrich_aggregations(measures, semantic_context)

    filters = _build_filter_specs(resolved.get("filters", []), raw_measures, semantic_context)
    time_filter = await _build_time_filter(resolved.get("timeframe"), anchor_tables, semantic_context, state)

    # Auto-generate FilterSpec from entity_hints that the LLM didn't already produce.
    # Only inject for tables already in anchor_tables — entity hints for non-anchor tables
    # are schema discovery signals, not query filters. Injecting them produces EXISTS
    # subqueries on unrelated tables, the exact hallucination NO_EXTRA_FILTERS prohibits.
    # matched_value may be a raw "CODE -> Human Name" alias entry — extract just the CODE.
    from app.services.agents.context.column_loader import _extract_db_code
    existing_filter_cols = {(f.table_fqn, f.column_name) for f in filters}
    for hint in (semantic_context or {}).get("entity_hints", []):
        key = (hint.get("table_fqn", ""), hint.get("column", ""))
        if key[0] not in anchor_tables:
            logger.debug(
                "ir_builder | entity_hint_skipped (not anchor) | {}.{}", key[0], key[1],
            )
            continue
        if key[0] and key[1] and key not in existing_filter_cols:
            raw_mv = hint["matched_value"]
            clean_mv = _extract_db_code(str(raw_mv)) if raw_mv else raw_mv
            filters.append(FilterSpec(
                table_fqn=key[0],
                column_name=key[1],
                operator="=",
                value=clean_mv,
                raw_user_value=hint.get("token", clean_mv),
                resolved=True,
                is_raw_sql=False,
                is_having=False,
            ))
            existing_filter_cols.add(key)
            logger.info(
                "ir_builder | entity_hint_filter | {}.{} = '{}' (raw='{}')",
                key[0], key[1], clean_mv, raw_mv,
            )

    template_id = resolved.get("template_id", "")
    cte_steps = _get_cte_steps(template_id, semantic_context)

    # temporal_grains: read list from resolved, fall back to single temporal_grain str.
    # Flatten nested lists — the LLM occasionally emits [["month"]] instead of ["month"].
    raw_grains = resolved.get("temporal_grains")
    if isinstance(raw_grains, list) and raw_grains:
        temporal_grains = []
        for g in raw_grains:
            if isinstance(g, list):
                temporal_grains.extend(str(x) for x in g if x)
            elif g:
                temporal_grains.append(str(g))
    else:
        single = resolved.get("temporal_grain") or _infer_temporal_grain(resolved.get("timeframe"))
        temporal_grains = [single] if single else []

    from app.services.agents.semantic_ir import DerivedMeasure, ThresholdSpec, CTEStepSpec

    # Populate richer IR fields from resolved intent when present.
    # These are used by complex queries (forecasts, threshold checks, multi-step CTEs).
    # ir_builder was silently dropping them — SQL generator had to reconstruct from directive text.
    derived_measures = [
        DerivedMeasure(**dm) for dm in (resolved.get("derived_measures") or [])
        if isinstance(dm, dict) and "alias" in dm and "expression" in dm
    ]
    threshold_specs = [
        ThresholdSpec(**ts) for ts in (resolved.get("threshold_specs") or [])
        if isinstance(ts, dict) and "expression" in ts and "operator" in ts and "value" in ts
    ]
    cte_chain = [
        CTEStepSpec(**cs) for cs in (resolved.get("cte_chain") or [])
        if isinstance(cs, dict) and "name" in cs
    ]

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
        temporal_grains=temporal_grains,
        cte_steps=cte_steps,
        derived_measures=derived_measures,
        threshold_specs=threshold_specs,
        cte_chain=cte_chain,
        order_by=_coerce_list(resolved.get("order_by")),
        limit=resolved.get("limit"),
        sub_query_index=None,
        candidate_join_paths=candidate_join_paths or None,
        unresolved_join_pairs=unresolved_pairs,
    )

    # Backstop: inject DATE_TRUNC dimension when grain is known but LLM didn't include the date
    # column in dimensions. Always fires for non-KPI shapes when time_filter is set —
    # users expect to see the date axis regardless of whether they named the column explicitly.
    if (
        ir.temporal_grain
        and ir.time_filter
        and ir.result_shape != "kpi"
        and not any(d.column_name == ir.time_filter.column_name for d in ir.dimensions)
    ):
        period_alias = f"period_{ir.temporal_grain}"
        ir.dimensions.insert(0, ColumnRef(
            table_fqn=ir.time_filter.table_fqn,
            column_name=ir.time_filter.column_name,
            alias=period_alias,
            semantic_type="date",
        ))
        logger.info(
            "ir_builder | injected date dim | grain={} | col={} | alias={}",
            ir.temporal_grain, ir.time_filter.column_name, period_alias,
        )

    # Inferred dimensions: store entity filter columns so SQL generator shows context cols
    ir.inferred_dimensions = [
        ColumnRef(
            table_fqn=hint.get("table_fqn", ""),
            column_name=hint.get("column", ""),
            alias=hint.get("column", ""),
            semantic_type="identifier",
        )
        for hint in (semantic_context or {}).get("entity_hints", [])
        if hint.get("table_fqn") and hint.get("column")
    ]

    if unresolved_pairs:
        logger.warning(
            "ir_builder | unresolved_join_pairs | count={} | pairs={}",
            len(unresolved_pairs), [(p["from"], p["to"]) for p in unresolved_pairs],
        )

    logger.info(
        "ir_builder | ir_built | template={} | anchor_tables={} | measures={} | time_filter={}.{} | "
        "filters={} | temporal_grains={} | unresolved_pairs={}",
        template_id,
        anchor_tables,
        [(m.column_name, m.aggregation) for m in measures],
        time_filter.table_fqn if time_filter else None,
        time_filter.column_name if time_filter else None,
        [(f.column_name, f.operator, str(f.value)[:20], f.is_having) for f in filters],
        temporal_grains,
        len(unresolved_pairs),
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


_UUID_SUFFIXES_IR = ("_uuid", "_guid", "_uid")


def _is_uuid_col_ir(col_name: str) -> bool:
    n = col_name.lower()
    return n == "uuid" or any(n.endswith(s) for s in _UUID_SUFFIXES_IR)


def _path_has_uuid_clauses(path: dict) -> bool:
    """True if any join clause in the path references a UUID column."""
    for clause in (path.get("join_clauses") or []):
        for part in clause.split("="):
            col = part.strip().rsplit(".", 1)[-1]
            if _is_uuid_col_ir(col):
                return True
    return False


def _pick_valid_primary_path(paths: list[dict]) -> dict | None:
    """Return the first path whose join columns are confirmed valid in the Redis schema cache.

    Prefers non-UUID paths — UUID-based joins always return 0 rows (UUIDs are unique per row).
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

    # Prefer non-UUID paths — UUID joins always return 0 rows
    non_uuid = [p for p in paths if not _path_has_uuid_clauses(p)]
    uuid_paths = [p for p in paths if _path_has_uuid_clauses(p)]

    for path in non_uuid:
        if _cols_valid(path):
            return path

    # UUID paths as last resort only
    for path in uuid_paths:
        if _cols_valid(path):
            logger.warning(
                "ir_builder | uuid_path_selected | tier={} | clauses={}",
                path.get("tier"), path.get("join_clauses"),
            )
            return path

    return next(iter(paths), None)


def _load_join_paths(anchor_tables: list[str], intent_directive: str = "") -> tuple[list, list, list, list, list, list]:
    """Load join paths for consecutive table pairs.

    Collects ALL available paths via collect_all_join_paths (JOINS_TO + dijkstra k1-3
    forward/reverse + yens k1-3 forward/reverse). Primary join_clauses are set from the
    first-priority path (JOINS_TO → dijkstra k=1 → yens k=1) for backward compat.
    All paths are stored in candidate_join_paths for the SQL generator to choose from.

    When no direct path exists between a pair, tries a 2-hop bridge via find_bridge_table().
    If a bridge is found, it is inserted into the pair list and re-resolved.
    If no bridge found, the pair is added to unresolved_join_pairs — NOT an empty string.

    Returns: (join_path_ids, join_clauses, path_tables, join_types, candidate_join_paths, unresolved_pairs)
    """
    if len(anchor_tables) <= 1:
        return [], [], list(anchor_tables), [], [], []

    join_path_ids: list[str] = []
    all_join_clauses: list[str] = []
    all_path_tables: list[str] = [anchor_tables[0]]
    join_types: list[str] = []
    candidate_join_paths: list[dict] = []
    unresolved_pairs: list[dict] = []

    # Pre-parse explicit JOIN_PATH clauses from intent directive — Tier 0 (highest priority).
    # Format: "JOIN_PATH: lpp.borrowing.facility_ref = lpp.credit_facility.code"
    # Bidirectional: stored for both (from,to) and (to,from) orderings.
    _intent_joins: dict[tuple[str, str], str] = {}
    if intent_directive:
        for _line in intent_directive.splitlines():
            if _line.strip().upper().startswith("JOIN_PATH:"):
                _m = _re.search(r"(\w+\.\w+)\.(\w+)\s*=\s*(\w+\.\w+)\.(\w+)", _line)
                if _m:
                    _f, _t = _m.group(1), _m.group(3)
                    _clause = f"{_m.group(1)}.{_m.group(2)} = {_m.group(3)}.{_m.group(4)}"
                    _intent_joins[(_f, _t)] = _clause
                    _intent_joins[(_t, _f)] = _clause

    # Work on a mutable copy — bridge insertions may expand the list
    work_tables = list(anchor_tables)
    i = 0
    while i < len(work_tables) - 1:
        from_table = work_tables[i]
        to_table = work_tables[i + 1]

        all_paths = neo4j_client.collect_all_join_paths(from_table, to_table)

        # Tier 0: inject intent directive JOIN_PATH at the front (highest priority)
        if (from_table, to_table) in _intent_joins:
            intent_clause = _intent_joins[(from_table, to_table)]
            all_paths.insert(0, {
                "id": "", "join_clauses": [intent_clause],
                "path_tables": [from_table, to_table], "hop_count": 1,
                "tier": "intent_directive", "direction": "forward",
                "from_fqn": from_table, "to_fqn": to_table,
            })
            logger.info(
                "ir_builder | intent_join_injected | from={} to={} | clause={}",
                from_table, to_table, intent_clause,
            )

        for p in all_paths:
            p.setdefault("from_fqn", from_table)
            p.setdefault("to_fqn", to_table)
        candidate_join_paths.extend(all_paths)

        jp = _pick_valid_primary_path(all_paths)
        if not jp:
            # Tier A: value-overlap join — data-driven FK discovery from distinct_values.
            # Preferred over bridge-table search: it verifies actual shared values rather
            # than relying on graph topology (which picks high-in_degree tables like fraud_loss).
            try:
                overlap_cols = neo4j_client.find_join_by_value_overlap(from_table, to_table)
            except Exception:
                overlap_cols = []

            if overlap_cols:
                best = overlap_cols[0]
                clause = f"{from_table}.{best['from_col']} = {to_table}.{best['to_col']}"
                all_join_clauses.append(clause)
                join_types.append("JOIN")
                if to_table not in all_path_tables:
                    all_path_tables.append(to_table)
                logger.info(
                    "ir_builder | value_overlap_join | from={} to={} | clause={} | overlap={}",
                    from_table, to_table, clause, best["overlap_count"],
                )
                i += 1
                continue

            # Tier B: 2-hop bridge via JOINS_TO-JOINS_TO (dimension/reference tables only).
            # Fact/event bridge tables are excluded by find_bridge_table's Cypher filter.
            bridge_fqn = neo4j_client.find_bridge_table(from_table, to_table)
            if bridge_fqn and bridge_fqn not in work_tables:
                work_tables.insert(i + 1, bridge_fqn)
                logger.info(
                    "ir_builder | bridge_inserted | bridge={} | between={} and {}",
                    bridge_fqn, from_table, to_table,
                )
                continue
            else:
                logger.warning(
                    "ir_builder | unresolved_pair | from={} to={} | no path, overlap, or bridge found",
                    from_table, to_table,
                )
                # Populate candidate_join_columns from value_overlap results even when they
                # weren't strong enough to resolve the join — gives sql_generator concrete
                # column candidates instead of a blank "no_path" signal.
                candidate_cols = [
                    f"{from_table}.{r['from_col']} = {to_table}.{r['to_col']}"
                    for r in (overlap_cols or [])[:3]
                ]
                unresolved_pairs.append({
                    "from": from_table,
                    "to": to_table,
                    "reason": "no_path",
                    "candidate_join_columns": candidate_cols,
                })
                i += 1
                continue

        logger.info(
            "ir_builder | join resolved | from={} to={} | tier={} | hops={} | paths={} | clauses={}",
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

        i += 1

    return join_path_ids, all_join_clauses, all_path_tables, join_types, candidate_join_paths, unresolved_pairs


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

    # Use get_filter_values() for consistent vocabulary assembly (distinct_values primary)
    filter_values = _get_filter_values(col_meta)

    # Build value_aliases map: {DB_code: human_name} — parse " -> " separator
    # DB code is left side, human label is right side
    raw_aliases = col_meta.get("value_aliases") or []
    alias_map: dict[str, str] = {}
    for a in raw_aliases:
        if isinstance(a, str) and " -> " in a:
            parts = a.split(" -> ", 1)
            alias_map[parts[0].strip()] = parts[1].strip()

    if not filter_values and not alias_map:
        return operator, raw_values

    resolved, modes = [], []
    for raw in raw_values:
        raw_lower = str(raw).lower().strip()

        # Alias reverse lookup: human label → DB code (runs even when filter_values is empty)
        if alias_map:
            # Handle raw "CODE -> Human Name" format — LLM sometimes outputs the full alias entry
            if " -> " in raw:
                code_part = raw.split(" -> ")[0].strip()
                matched_raw = next((k for k in alias_map if k.lower() == code_part.lower()), None)
                if matched_raw:
                    logger.info("ir_builder | filter alias_raw_fmt | {}.{} | {} → {}", table_fqn, column, raw, matched_raw)
                    resolved.append(matched_raw)
                    modes.append("exact")
                    continue

            # User said the human label (e.g., "Closing Balance") → return DB code ("CLOSING")
            db_code = next((k for k, v in alias_map.items() if v.lower() == raw_lower), None)
            if db_code:
                logger.info("ir_builder | filter alias_reverse | {}.{} | {} → {}", table_fqn, column, raw, db_code)
                resolved.append(db_code)
                modes.append("exact")
                continue
            # User said the DB code directly (e.g., "CLOSING") → keep as-is
            matched_key = next((k for k in alias_map if k.lower() == raw_lower), None)
            if matched_key:
                logger.info("ir_builder | filter alias_exact | {}.{} | {} → {}", table_fqn, column, raw, matched_key)
                resolved.append(matched_key)
                modes.append("exact")
                continue

        if not filter_values:
            resolved.append(raw)
            modes.append("unknown")
            continue

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

        if "," in str(raw):
            logger.debug(
                "ir_builder | filter multi-value deferring to downstream split | {}.{} | value={}",
                table_fqn, column, raw,
            )
        else:
            logger.warning(
                "ir_builder | filter value NOT in Redshift distinct values | {}.{} | value={} | sample={}",
                table_fqn, column, raw, filter_values[:5],
            )
        resolved.append(raw)
        modes.append("unknown")

    if all(m == "unknown" for m in modes):
        return "UNRESOLVED", resolved

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

        # Type-aware fast path: boolean, numeric — deterministic resolution, bypasses vocab matching
        type_spec = _type_aware_filter_spec(f, col_name, raw_value, raw_op, semantic_context, is_having)
        if type_spec is not None:
            filters.append(type_spec)
            continue

        already_a_pattern = raw_op in ("LIKE", "ILIKE") and isinstance(raw_value, str) and "%" in raw_value
        if not is_comparison and not already_a_pattern and raw_op in ("=", "IN", "LIKE", "ILIKE") and isinstance(raw_value, str):
            raw_list = [raw_value]
            norm_op, norm_values = _resolve_filter_values(
                col_name, f["table_fqn"], raw_list, raw_op, semantic_context,
            )
            if norm_op == "UNRESOLVED":
                for v in norm_values:
                    filters.append(FilterSpec(
                        table_fqn=f["table_fqn"],
                        column_name=col_name,
                        operator="=",
                        value=v,
                        raw_user_value=raw_value,
                        resolved=False,
                        is_having=is_having,
                    ))
                continue
            elif norm_op == "ILIKE_MULTI":
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


async def _build_time_filter(
    timeframe,
    anchor_tables: list[str],
    semantic_context: dict,
    state: dict | None = None,
) -> FilterSpec | None:
    """Resolve a timeframe expression to a FilterSpec.

    `timeframe` may arrive as:
      - str  "next_90_days"  — standard case, resolved via tier3 + LLM fallback
      - dict {"operator": "BETWEEN_SQL", "value": [...], ...}  — pre-resolved by intent node
      - None — no time filter

    Resolution order for str:
    1. Sync pre-check (deterministic): today / yesterday / ISO date / ISO range.
    2. _tier35_temporal_llm (Haiku) for any natural-language expression.
    """
    if not timeframe:
        return None

    intent_directive = (state or {}).get("intent_directive") or ""
    table_fqn, date_col = _find_date_column(anchor_tables, semantic_context, intent_directive)

    # Already-resolved dict: an upstream node pre-resolved the timeframe.
    # Use it directly without another resolution pass.
    if isinstance(timeframe, dict):
        op = timeframe.get("operator")
        val = timeframe.get("value") or timeframe.get("start")  # handle {start, end} format too
        if op and val is not None:
            if timeframe.get("end") and isinstance(val, str):
                val = [val, timeframe["end"]]
            return FilterSpec(
                table_fqn=table_fqn,
                column_name=date_col,
                operator=op,
                value=val,
                raw_user_value=str(timeframe),
                resolved=True,
                is_raw_sql=timeframe.get("is_raw_sql", False),
            )
        # dict but missing required fields — fall through to string path with str(dict)
        timeframe = str(timeframe)

    from app.services.agents.filter_resolver_logic import resolve_tier3_temporal
    result = resolve_tier3_temporal(timeframe)

    if result is None:
        from app.services.agents.nodes.filter_resolver import _tier35_temporal_llm
        result = await _tier35_temporal_llm(timeframe, state or {})

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


_DATE_GRAINS = {"day", "week", "month", "quarter", "year", "hour", "minute", "date"}


def _find_date_column(
    anchor_tables: list[str],
    semantic_context: dict,
    intent_directive: str = "",
) -> tuple[str, str]:
    """Metadata-driven date column selection — three ranked passes, no hardcoded names.

    Pass 0: TIME_FILTER column in intent directive — highest priority.
    Pass 1: column.temporal_grain is set — returns FIRST match as default.
            When multiple temporal columns exist on the primary table, the SCHEMA DIRECTIVE
            emits the full TEMPORAL COLUMNS list so directive_writer can pick the right one.
    Pass 2: any date/timestamp data_type (last resort).
    """
    import re as _re
    columns = semantic_context.get("columns", [])

    # Pass 0: column named in intent directive TIME_FILTER line wins over metadata.
    if intent_directive:
        for line in intent_directive.splitlines():
            if line.strip().upper().startswith("TIME_FILTER:"):
                m = _re.search(r"(\w+)\.(\w+)\.(\w+)\s*(?:BETWEEN|>=|<=|=|>|<)", line)
                if m:
                    candidate_fqn = f"{m.group(1)}.{m.group(2)}"
                    candidate_col = m.group(3)
                    if candidate_fqn in anchor_tables:
                        for c in columns:
                            if c.get("table_fqn") == candidate_fqn and c.get("name") == candidate_col:
                                logger.info(
                                    "ir_builder | date_col | table={} col={} via=intent_directive",
                                    candidate_fqn, candidate_col,
                                )
                                return candidate_fqn, candidate_col
                break

    _DATE_TYPES = {"date", "timestamp", "datetime"}
    _DATE_SEMANTICS = {"date", "datetime", "timestamp"}

    # Pass 1: column.temporal_grain is set — return first match as default.
    # Multiple candidates are exposed via TEMPORAL COLUMNS in _build_schema_directive.
    for table in anchor_tables:
        for col in columns:
            if col.get("table_fqn") != table:
                continue
            if col.get("temporal_grain", "").lower() in _DATE_GRAINS:
                logger.info("ir_builder | date_col | table={} col={} via=temporal_grain", table, col["name"])
                return table, col["name"]

    # Pass 2: any date/timestamp data_type (last resort)
    for table in anchor_tables:
        for col in columns:
            if col.get("table_fqn") != table:
                continue
            if col.get("data_type", "").lower() in _DATE_TYPES:
                logger.info("ir_builder | date_col | table={} col={} via=data_type(fallback)", table, col["name"])
                return table, col["name"]
            if col.get("semantic_type", "").lower() in _DATE_SEMANTICS:
                logger.info("ir_builder | date_col | table={} col={} via=semantic_type(fallback)", table, col["name"])
                return table, col["name"]

    fallback_table = anchor_tables[0] if anchor_tables else ""
    logger.warning("ir_builder | date_col not found | anchor_tables={} | fallback={}.transaction_date", anchor_tables, fallback_table)
    return fallback_table, "transaction_date"


def _get_cte_steps(template_id: str, semantic_context: dict) -> list[str]:
    return []

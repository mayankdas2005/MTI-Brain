"""Column loading with join-critical prioritization.

Key design decisions:
- filter_values built from all three vocabulary sources (distinct_values primary)
- _column_lookup is built from UNTRIMMED full data
- UUID columns stripped everywhere — they are internal row IDs, never join keys or filters
- trim_objects() applies only to display columns shown to LLM
- _join_critical columns are guaranteed T1 priority in merge
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents import neo4j_client

MAX_PER_TABLE = 12
GLOBAL_CAP    = 80

# UUID column detection — these are internal row identifiers, never valid for joins/filters
_UUID_SUFFIXES = ("_uuid", "_guid", "_uid")


def _is_uuid_col(col_name: str) -> bool:
    """True if column is a UUID/GUID/UID — internal row identifier, useless for joins."""
    if not col_name:
        return False
    n = col_name.lower()
    return n == "uuid" or any(n.endswith(s) for s in _UUID_SUFFIXES)


def _extract_db_code(raw: str) -> str:
    """If raw is 'CODE -> Human Name', return just CODE. Otherwise return raw unchanged."""
    if " -> " in raw:
        return raw.split(" -> ")[0].strip()
    return raw


def get_filter_values(col_meta: dict) -> list[str]:
    """Assemble all available DB codes for filter value matching.

    Priority: distinct_values (actual Redshift stats) → value_vocabulary (may be LLM-generated)
    → left-side codes from value_aliases.
    All three sources are combined and deduplicated.
    Any 'CODE -> Human Name' format is stripped to just the CODE in all three sources.
    """
    distinct = list(col_meta.get("distinct_values") or [])
    vocab    = list(col_meta.get("value_vocabulary") or [])
    alias_codes = []
    for a in (col_meta.get("value_aliases") or []):
        if isinstance(a, str) and " -> " in a:
            code = a.split(" -> ")[0].strip()
            if code:
                alias_codes.append(code)

    seen: set[str] = set()
    result: list[str] = []
    for v in distinct + vocab + alias_codes:
        sv = _extract_db_code(str(v).strip()) if v is not None else ""
        if sv and sv not in seen:
            seen.add(sv)
            result.append(sv)
    return result


def get_join_critical_cols(tables: list[dict]) -> set[tuple]:
    """Get join-critical (table_fqn, col_name) pairs using 4 sources.

    Sources A+B+C+D are all Cypher-based (see neo4j/column_search.py).
    UUID columns are stripped — they are surrogate keys, never valid join columns.
    """
    fqns = [t["fqn"] for t in tables if t.get("fqn")]
    if not fqns:
        return set()

    join_critical: set[tuple] = set()

    try:
        rows = neo4j_client.get_join_critical_columns(fqns)
        for r in rows:
            if isinstance(r, tuple):
                fqn, col = r
            elif isinstance(r, dict) and r.get("table_fqn") and r.get("col_name"):
                fqn, col = r["table_fqn"], r["col_name"]
            else:
                continue
            if not _is_uuid_col(col):
                join_critical.add((fqn, col))
        logger.debug("column_loader | join_critical from graph | count={}", len(join_critical))
    except Exception as e:
        logger.warning("column_loader | get_join_critical_columns failed | error={}", e)

    # Augment from table metadata — time_dimension_col only (pk_columns excluded: always uuid)
    for t in tables:
        fqn = t.get("fqn", "")
        tdim = t.get("time_dimension_col")
        if tdim and not _is_uuid_col(tdim):
            join_critical.add((fqn, tdim))

    logger.info("column_loader | join_critical_total | count={}", len(join_critical))
    return join_critical


def load_and_prioritize(
    tables: list[dict],
    embedding: list[float],
    search_query: str,
    join_critical_cols: set[tuple],
) -> tuple[list[dict], dict]:
    """Load all columns, mark join-critical, build filter_values, build _column_lookup.

    Returns: (display_columns, _column_lookup)
    - display_columns: per-table prioritized T1-T4, capped at MAX_PER_TABLE and GLOBAL_CAP
    - _column_lookup: full untrimmed dict keyed by (table_fqn, col_name)

    UUID columns are stripped from BOTH display_columns and _column_lookup.
    """
    candidate_fqns = {t["fqn"] for t in tables if t.get("fqn")}
    if not candidate_fqns:
        return [], {}

    # Load full column data from Neo4j
    columns_graph = neo4j_client.get_columns_for_tables(list(candidate_fqns))
    columns_v     = neo4j_client.search_columns_vector(embedding)
    columns_fts   = neo4j_client.search_columns_fulltext(search_query)

    logger.info("column_loader | cols_graph={} | cols_vector={} | cols_fts={}",
                len(columns_graph), len(columns_v), len(columns_fts))

    # Strip UUID columns immediately — they are never useful for joins, filters, or display
    columns_graph = [c for c in columns_graph if not _is_uuid_col(c.get("name", ""))]

    # Mark join-critical BEFORE any processing
    for col in columns_graph:
        key = (col.get("table_fqn"), col.get("name"))
        col["_join_critical"] = key in join_critical_cols

    # Build filter_values from all vocabulary sources (distinct_values is primary)
    for col in columns_graph:
        col["filter_values"] = get_filter_values(col)

    # Build _column_lookup from FULL UNTRIMMED data (no UUID cols)
    column_lookup = {
        (col["table_fqn"], col["name"]): col
        for col in columns_graph
        if col.get("table_fqn") and col.get("name")
    }

    # Build per-table semantic scores from vector + fts (exclude UUID cols)
    semantic_scores: dict[tuple, float] = {}
    for c in columns_v:
        if _is_uuid_col(c.get("name", "")):
            continue
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] in candidate_fqns:
            semantic_scores[key] = max(semantic_scores.get(key, 0.0), c.get("score") or 0.0)
    for c in columns_fts:
        if _is_uuid_col(c.get("name", "")):
            continue
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] in candidate_fqns:
            semantic_scores[key] = max(semantic_scores.get(key, 0.0), (c.get("score") or 0.0) + 0.05)

    # Per-table selection with T1-T4 priority
    table_priority = {t["fqn"]: len(t.get("retrieval_paths") or []) for t in tables}
    display_columns = _merge_column_sources(
        columns_graph, semantic_scores, candidate_fqns, table_priority
    )

    return display_columns, column_lookup


def load_for_bridge_tables(
    bridge_fqns: list[str],
    embedding: list[float],
    search_query: str,
    join_critical_cols: set[tuple],
) -> tuple[list[dict], dict]:
    """Load columns for bridge/intermediate tables added during join expansion.

    These tables weren't in the initial discovery so weren't loaded by load_and_prioritize.
    Uses same logic but with a smaller per-table cap (bridge tables need fewer cols for display).
    """
    if not bridge_fqns:
        return [], {}

    bridge_tables = [{"fqn": fqn, "retrieval_paths": ["bridge_table"]} for fqn in bridge_fqns]
    display_cols, col_lookup = load_and_prioritize(
        bridge_tables, embedding, search_query, join_critical_cols
    )
    # Bridge tables only need up to 8 display columns (join cols + key measures)
    by_table: dict[str, list[dict]] = {}
    for col in display_cols:
        by_table.setdefault(col.get("table_fqn", ""), []).append(col)
    bridge_display = []
    for fqn in bridge_fqns:
        bridge_display.extend(by_table.get(fqn, [])[:8])

    logger.info("column_loader | bridge_cols_loaded | fqns={} | total_cols={}", bridge_fqns, len(bridge_display))
    return bridge_display, col_lookup


def _merge_column_sources(
    graph_cols: list[dict],
    semantic_scores: dict[tuple, float],
    candidate_fqns: set[str],
    table_priority: dict[str, int],
) -> list[dict]:
    """Per-table T1-T4 prioritized column selection.

    T1: _join_critical columns — guaranteed for EVERY table regardless of GLOBAL_CAP.
        Without this guarantee, tables ranked 8-14 get zero columns when the global
        cap is hit by earlier tables, leaving the LLM blind to join keys it needs.
    T2: semantically matched by question (from vector + fts)
    T3: is_measurable or is_groupable
    T4: everything else

    MAX_PER_TABLE=12, GLOBAL_CAP=80 (T2-T4 only; T1 is additive)
    """
    by_table: dict[str, list[dict]] = {}
    for col in graph_cols:
        fqn = col.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(col)

    # Phase 1: collect T1 (join-critical) for ALL tables — no cap applies
    guaranteed: list[dict] = []
    guaranteed_keys: set[tuple] = set()
    for fqn in sorted(by_table, key=lambda f: table_priority.get(f, 0), reverse=True):
        for col in by_table[fqn]:
            if col.get("_join_critical"):
                guaranteed.append(col)
                guaranteed_keys.add((col.get("table_fqn"), col.get("name")))

    # Phase 2: fill remaining budget with T2-T4 in table-priority order
    budget = max(0, GLOBAL_CAP - len(guaranteed))
    optional: list[dict] = []

    for fqn in sorted(by_table, key=lambda f: table_priority.get(f, 0), reverse=True):
        if budget <= 0:
            break
        cols = by_table[fqn]
        t1_count = sum(1 for c in cols if c.get("_join_critical"))

        t2 = sorted(
            [c for c in cols if not c.get("_join_critical") and (fqn, c.get("name")) in semantic_scores],
            key=lambda c: semantic_scores.get((fqn, c.get("name", "")), 0.0),
            reverse=True,
        )
        t3 = [
            c for c in cols
            if not c.get("_join_critical")
            and (fqn, c.get("name")) not in semantic_scores
            and (c.get("is_measurable") or c.get("is_groupable"))
        ]
        t4 = [c for c in cols if c not in [cc for cc in cols if cc.get("_join_critical")] + t2 + t3]

        non_t1 = (t2 + t3 + t4)[:max(0, MAX_PER_TABLE - t1_count)]
        take = min(len(non_t1), budget)
        optional.extend(non_t1[:take])
        budget -= take

    return guaranteed + optional

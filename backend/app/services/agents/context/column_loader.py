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

    logger.info("column_loader | join_critical_total | count={}", len(join_critical))
    return join_critical


def load_and_prioritize(
    tables: list[dict],
    embedding: list[float],
    search_query: str,
    join_critical_cols: set[tuple],
    entity_tokens: list[str] | None = None,
) -> tuple[list[dict], dict, set[str]]:
    """Load all columns, mark join-critical, build filter_values, build _column_lookup.

    Returns: (display_columns, _column_lookup, entity_col_tables)
    - display_columns: per-table prioritized T1-T4, capped at MAX_PER_TABLE
    - _column_lookup: full untrimmed dict keyed by (table_fqn, col_name)
    - entity_col_tables: parent table FQNs of columns matched by per-entity FTS

    UUID columns are stripped from BOTH display_columns and _column_lookup.
    """
    candidate_fqns = {t["fqn"] for t in tables if t.get("fqn")}
    if not candidate_fqns:
        return [], {}, set()

    logger.info("column_loader START | candidate_tables={} | candidate_fqns={}",
                len(candidate_fqns), sorted(candidate_fqns))

    # Load full column data from Neo4j
    columns_graph = neo4j_client.get_columns_for_tables(list(candidate_fqns))
    columns_v     = neo4j_client.search_columns_vector(embedding)
    columns_fts   = neo4j_client.search_columns_fulltext(search_query)

    logger.info("column_loader | cols_graph={} | cols_vector={} | cols_fts_fullquestion={}",
                len(columns_graph), len(columns_v), len(columns_fts))

    # Per-entity column FTS — separate tracked pass for each entity token
    entity_col_fts: list[dict] = []
    entity_col_tables: set[str] = set()
    for ent in (entity_tokens or [])[:4]:
        ent_cols = neo4j_client.search_columns_fulltext(ent)
        if ent_cols:
            logger.info("column_loader | entity_col_fts | entity={} | cols={} | parent_tables={}",
                ent, [c.get("name") for c in ent_cols[:5]],
                sorted({c.get("table_fqn") for c in ent_cols if c.get("table_fqn")}))
            entity_col_fts.extend(ent_cols)
            entity_col_tables |= {c["table_fqn"] for c in ent_cols if c.get("table_fqn")}

    if entity_col_tables:
        logger.info("column_loader | entity_col_fts_total | entities={} | total_cols={} | entity_col_tables={}",
                    len(entity_tokens or []), len(entity_col_fts), sorted(entity_col_tables))

    # Build fts_boosted_ids — columns from vector + full-question FTS + entity FTS go to Bucket 1
    fts_boosted_ids: set[tuple] = {
        (c.get("table_fqn"), c.get("name"))
        for c in columns_v + columns_fts + entity_col_fts
        if c.get("table_fqn") and c.get("name")
    }
    logger.info("column_loader | fts_boosted_ids | count={}", len(fts_boosted_ids))

    # Strip UUID columns immediately — they are never useful for joins, filters, or display
    columns_graph = [c for c in columns_graph if not _is_uuid_col(c.get("name", ""))]

    # Mark join-critical BEFORE any processing
    for col in columns_graph:
        key = (col.get("table_fqn"), col.get("name"))
        col["_join_critical"] = key in join_critical_cols

    # Mark FTS-boosted columns (Bucket 1 eligible even if not join-critical)
    for col in columns_graph:
        key = (col.get("table_fqn"), col.get("name"))
        col["_fts_boosted"] = key in fts_boosted_ids

    # Build filter_values from all vocabulary sources (distinct_values is primary)
    for col in columns_graph:
        col["filter_values"] = get_filter_values(col)

    # Build _column_lookup from FULL UNTRIMMED data (no UUID cols)
    column_lookup = {
        (col["table_fqn"], col["name"]): col
        for col in columns_graph
        if col.get("table_fqn") and col.get("name")
    }

    # Per-table selection with 2-bucket priority, capped at MAX_PER_TABLE per table
    display_columns = _merge_column_sources(columns_graph, candidate_fqns)

    logger.info("column_loader DONE | display_cols={} | entity_col_tables={}",
                len(display_columns), sorted(entity_col_tables))

    return display_columns, column_lookup, entity_col_tables


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
    display_cols, col_lookup, _ = load_and_prioritize(
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


_SEM_ORDER = {
    "amount": 0, "measure": 0, "percentage": 0, "ratio": 0,
    "dimension": 1, "code": 1, "flag": 1,
    "identifier": 2,
    "free_text": 3,
}


def _merge_column_sources(graph_cols: list[dict], candidate_fqns: set[str]) -> list[dict]:
    """Per-table 2-bucket priority selection, capped at MAX_PER_TABLE.

    Bucket 1: join-critical columns (FK side and source side) — always included
    Bucket 2: remaining analytical columns sorted by semantic_type value
    """
    by_table: dict[str, list[dict]] = {}
    for col in graph_cols:
        fqn = col.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(col)

    result: list[dict] = []
    for fqn in sorted(by_table):
        cols = by_table[fqn]
        result.extend(_select_top_columns(cols, MAX_PER_TABLE))
    return result


def _select_top_columns(cols: list[dict], max_n: int = MAX_PER_TABLE) -> list[dict]:
    """2-bucket column selection for a single table."""
    def col_id(c: dict) -> tuple:
        return (c.get("table_fqn", ""), c.get("name", ""))

    # Bucket 1: join-critical (graph-marked), FK column, or FTS-boosted (vector/entity match)
    bucket1 = [c for c in cols if c.get("_join_critical") or c.get("referenced_table_fqn") or c.get("_fts_boosted")]
    b1_ids = {col_id(c) for c in bucket1}

    # Bucket 2: remaining, sorted by semantic value
    bucket2 = sorted(
        [c for c in cols if col_id(c) not in b1_ids],
        key=lambda c: _SEM_ORDER.get(c.get("semantic_type", ""), 4),
    )
    return (bucket1 + bucket2)[:max_n]

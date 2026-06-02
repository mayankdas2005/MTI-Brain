"""Column loading with join-critical prioritization.

Key design decisions:
- filter_values = value_vocabulary (not sample_values — too few values)
- _column_lookup is built from UNTRIMMED full data (preserves 30-item vocabulary)
- trim_objects() applies only to display columns shown to LLM
- _join_critical columns are guaranteed T1 priority in merge
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents import neo4j_client

MAX_PER_TABLE = 12
GLOBAL_CAP    = 80


def get_join_critical_cols(tables: list[dict]) -> set[tuple]:
    """Get join-critical (table_fqn, col_name) pairs using 4 sources.

    Sources A+B+C+D are all Cypher-based (see neo4j/column_search.py).
    Augmented with Table.pk_columns (non-surrogate) and time_dimension_col from table metadata.
    """
    fqns = [t["fqn"] for t in tables if t.get("fqn")]
    if not fqns:
        return set()

    join_critical: set[tuple] = set()

    try:
        rows = neo4j_client.get_join_critical_columns(fqns)
        for r in rows:
            if isinstance(r, tuple):
                join_critical.add(r)
            elif isinstance(r, dict) and r.get("table_fqn") and r.get("col_name"):
                join_critical.add((r["table_fqn"], r["col_name"]))
        logger.debug("column_loader | join_critical from graph | count={}", len(join_critical))
    except Exception as e:
        logger.warning("column_loader | get_join_critical_columns failed | error={}", e)

    # Augment with table metadata (pk_columns, time_dimension_col)
    for t in tables:
        fqn = t.get("fqn", "")
        for pk in (t.get("pk_columns") or []):
            if pk and pk != "uuid" and not pk.endswith("_uuid"):
                join_critical.add((fqn, pk))
        tdim = t.get("time_dimension_col")
        if tdim and tdim != "uuid":
            join_critical.add((fqn, tdim))

    logger.info("column_loader | join_critical_total | count={}", len(join_critical))
    return join_critical


def load_and_prioritize(
    tables: list[dict],
    embedding: list[float],
    search_query: str,
    join_critical_cols: set[tuple],
) -> tuple[list[dict], dict]:
    """Load all columns, mark join-critical, set filter_values, build _column_lookup.

    Returns: (display_columns, _column_lookup)
    - display_columns: per-table prioritized T1-T4, capped at MAX_PER_TABLE and GLOBAL_CAP
    - _column_lookup: full untrimmed dict keyed by (table_fqn, col_name)
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

    # Mark join-critical BEFORE any processing
    for col in columns_graph:
        key = (col.get("table_fqn"), col.get("name"))
        col["_join_critical"] = key in join_critical_cols

    # Set filter_values from value_vocabulary BEFORE trimming
    # value_vocabulary = distinct_values[0..30] from ingestion pipeline
    # sample_values has only 5-10 values — NOT sufficient for filter resolution
    for col in columns_graph:
        col["filter_values"] = col.get("value_vocabulary") or []

    # Build _column_lookup from FULL UNTRIMMED data
    column_lookup = {
        (col["table_fqn"], col["name"]): col
        for col in columns_graph
        if col.get("table_fqn") and col.get("name")
    }

    # Build per-table semantic scores from vector + fts
    semantic_scores: dict[tuple, float] = {}
    for c in columns_v:
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] in candidate_fqns:
            semantic_scores[key] = max(semantic_scores.get(key, 0.0), c.get("score") or 0.0)
    for c in columns_fts:
        key = (c.get("table_fqn"), c.get("name"))
        if key[0] in candidate_fqns:
            semantic_scores[key] = max(semantic_scores.get(key, 0.0), (c.get("score") or 0.0) + 0.05)

    # Per-table selection with T1-T4 priority
    table_priority = {t["fqn"]: len(t.get("retrieval_paths") or []) for t in tables}
    display_columns = _merge_column_sources(
        columns_graph, semantic_scores, candidate_fqns, table_priority
    )

    return display_columns, column_lookup


def _merge_column_sources(
    graph_cols: list[dict],
    semantic_scores: dict[tuple, float],
    candidate_fqns: set[str],
    table_priority: dict[str, int],
) -> list[dict]:
    """Per-table T1-T4 prioritized column selection.

    T1: _join_critical columns — always shown regardless of semantic score
    T2: semantically matched by question (from vector + fts)
    T3: is_measurable or is_groupable
    T4: everything else

    MAX_PER_TABLE=12, GLOBAL_CAP=80
    """
    by_table: dict[str, list[dict]] = {}
    for col in graph_cols:
        fqn = col.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(col)

    result: list[dict] = []

    for fqn in sorted(by_table, key=lambda f: table_priority.get(f, 0), reverse=True):
        cols = by_table[fqn]

        t1 = [c for c in cols if c.get("_join_critical")]
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
        t4 = [c for c in cols if c not in t1 + t2 + t3]

        selected = (t1 + t2 + t3 + t4)[:MAX_PER_TABLE]
        result.extend(selected)
        if len(result) >= GLOBAL_CAP:
            break

    return result[:GLOBAL_CAP]

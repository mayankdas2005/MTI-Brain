"""Column search and retrieval functions."""

from __future__ import annotations

import re
import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_run
from .table_search import _fuzzy_fts

# Regex to parse "lpp.table_name.col_name" from JoinPath join_clauses
_FQN_COL_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)")


@neo4j_breaker
def search_columns_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (c:Column)
    SEARCH c IN (VECTOR INDEX `col_cohere_embedding` FOR $embedding LIMIT 15)
    SCORE AS score
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.semantic_type AS semantic_type, 
           c.data_type AS data_type, 
           score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_columns_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_columns_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('col_ft_extended', $query)
    YIELD node AS c, score
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.semantic_type AS semantic_type,
           score LIMIT 15
    """
    try:
        t0 = time.monotonic()
        results = _neo4j_run(cypher, {"query": _fuzzy_fts(query_text)})
        logger.debug("neo4j | fn=search_columns_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_columns_fulltext fts failed | error={}", e)
        return []


@neo4j_breaker
def get_columns_for_tables(table_fqns: list[str]) -> list[dict]:
    """Load ALL columns for the given tables.

    Excludes surrogate key columns (uuid-named PKs) which have no join or analytical semantics.
    Includes referenced_table_fqn and referenced_column — these are YAML-sourced FK data,
    reliable even when is_foreign_key=False.
    """
    if not table_fqns:
        return []
    query = """
    MATCH (t:Table)-[:HAS_COLUMN]-(c:Column)
    WHERE t.fqn IN $table_fqns
    RETURN c.name AS name, coalesce(c.table_fqn, t.fqn) AS table_fqn,
           c.data_type AS data_type,
           c.semantic_type AS semantic_type,
           c.description AS description,
           c.sample_values AS sample_values,
           c.value_vocabulary AS value_vocabulary,
           c.value_aliases AS value_aliases,
           c.synonyms AS synonyms,
           c.null_frac AS null_frac,
           c.has_data AS has_data,
           c.ordinal_position AS ordinal_position,
           c.n_distinct AS n_distinct,

           c.top_freq_values AS top_freq_values,
           c.distinct_values AS distinct_values,
           c.is_nullable AS is_nullable,
           c.referenced_table_fqn AS referenced_table_fqn,
           c.referenced_column AS referenced_column
    ORDER BY coalesce(c.table_fqn, t.fqn), c.ordinal_position
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"table_fqns": table_fqns})
    logger.debug("neo4j | fn=get_columns_for_tables | ms={:.0f} | cols={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def get_join_critical_columns(fqns: list[str]) -> list[tuple[str, str]]:
    """Identify columns that are used in join relationships for the given tables.

    Uses 4 sources (all combined via UNION):
    A. Column.referenced_table_fqn IS NOT NULL — YAML FK data (reliable even when is_foreign_key=False)
    B. JOINS_TO from_col / to_col — direct FK edges from manually curated YAML joins
    C. hub_join_col on dimension hub tables
    D. JoinPath.join_clauses — intermediate columns in multi-hop pre-computed paths

    Returns: set of (table_fqn, col_name) tuples.
    """
    if not fqns:
        return []

    join_critical: set[tuple[str, str]] = set()

    # Sources A + B + C via single Cypher
    cypher = """
    // Source A: YAML FK references (referenced_table_fqn set from YAML)
    MATCH (c:Column)
    WHERE c.table_fqn IN $fqns
      AND c.referenced_table_fqn IS NOT NULL
      AND c.referenced_table_fqn <> ''
    RETURN c.table_fqn AS table_fqn, c.name AS col_name

    UNION

    // Source B: JOINS_TO edges — columns used in declared joins (both endpoints)
    MATCH (t:Table)-[r:JOINS_TO]->()
    WHERE t.fqn IN $fqns
    RETURN t.fqn AS table_fqn, r.from_col AS col_name

    UNION

    MATCH ()-[r:JOINS_TO]->(t:Table)
    WHERE t.fqn IN $fqns
    RETURN t.fqn AS table_fqn, r.to_col AS col_name

    UNION

    // Source C: hub_join_col on dimension hub tables
    MATCH (t:Table {is_dimension_hub: true})
    WHERE t.fqn IN $fqns
      AND t.hub_join_col IS NOT NULL
      AND t.hub_join_col <> ''
    RETURN t.fqn AS table_fqn, t.hub_join_col AS col_name
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(cypher, {"fqns": fqns})
        logger.debug("neo4j | fn=get_join_critical_columns | A+B+C | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            if r.get("table_fqn") and r.get("col_name"):
                join_critical.add((r["table_fqn"], r["col_name"]))
    except Exception as e:
        logger.warning("neo4j | get_join_critical_columns A+B+C failed | error={}", e)

    # Source D: JoinPath.join_clauses — extracts intermediate columns from multi-hop paths
    # Uses jp_from_fqn + jp_to_fqn indexes (added in Phase 1) for fast lookup
    joinpath_cypher = """
    MATCH (jp:JoinPath)
    WHERE (jp.from_fqn IN $fqns OR jp.to_fqn IN $fqns
           OR any(t IN jp.path_tables WHERE t IN $fqns))
      AND jp.hop_count <= 2
      AND jp.quality_score >= 0.75
    RETURN jp.join_clauses AS clauses
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(joinpath_cypher, {"fqns": fqns})
        logger.debug("neo4j | fn=get_join_critical_columns | D (JoinPath) | ms={:.0f} | paths={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            for clause in (r.get("clauses") or []):
                for m in _FQN_COL_RE.finditer(str(clause)):
                    table_fqn = f"{m.group(1)}.{m.group(2)}"
                    col_name = m.group(3)
                    join_critical.add((table_fqn, col_name))
    except Exception as e:
        logger.warning("neo4j | get_join_critical_columns D (JoinPath) failed | error={}", e)

    logger.debug("neo4j | get_join_critical_columns | total_pairs={}", len(join_critical))
    return list(join_critical)


@neo4j_breaker
def get_semantically_similar_columns(table_fqns: list[str]) -> list[dict]:
    if not table_fqns:
        return []
    query = """
    MATCH (c1:Column)-[r:SEMANTICALLY_SIMILAR]->(c2:Column)
    WHERE c1.table_fqn IN $fqns AND c2.table_fqn IN $fqns
    RETURN c1.id AS from_id, c2.id AS to_id, properties(r) AS rel
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": table_fqns})
    logger.debug("neo4j | fn=get_semantically_similar_columns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [{"from_id": r["from_id"], "to_id": r["to_id"], "rel": dict(r["rel"] or {})} for r in results]


@neo4j_breaker
def get_columns_by_ids(col_ids: list[str]) -> list[dict]:
    """Fetch full column properties for specific Column.id values (format: 'table_fqn.col_name').

    Used by graph_context_builder to fetch only the columns that were actually
    referenced in the SQL (measures, dimensions, filters) — not all table columns.
    """
    if not col_ids:
        return []
    query = """
    MATCH (c:Column) WHERE c.id IN $col_ids
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.data_type AS data_type, c.semantic_type AS semantic_type,
           c.description AS description, c.sample_values AS sample_values, c.distinct_values AS distinct_values,
           c.value_vocabulary AS value_vocabulary, c.synonyms AS synonyms
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"col_ids": col_ids})
    logger.debug("neo4j | fn=get_columns_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def find_join_by_value_overlap(from_fqn: str, to_fqn: str) -> list[dict]:
    """Find candidate join column pairs by distinct_value overlap.

    Checks every column pair (c1 from from_fqn, c2 from to_fqn) where both have
    stored distinct_values and at least one value appears in both lists.
    Returns rows sorted by overlap_count DESC — more shared values = stronger FK signal.

    Used when no JOINS_TO / JoinPath edge exists between two tables, as an alternative
    to the bridge-table search which can pick semantically wrong intermediaries.
    """
    query = """
    MATCH (t1:Table {fqn: $from_fqn})-[:HAS_COLUMN]->(c1:Column)
    MATCH (t2:Table {fqn: $to_fqn})-[:HAS_COLUMN]->(c2:Column)
    WHERE c1.distinct_values IS NOT NULL
      AND c2.distinct_values IS NOT NULL
      AND size(c1.distinct_values) > 0
      AND size(c2.distinct_values) > 0
      AND any(v IN c1.distinct_values WHERE v IN c2.distinct_values)
    WITH c1.name  AS from_col,
         c2.name  AS to_col,
         [v IN c1.distinct_values WHERE v IN c2.distinct_values] AS shared
    RETURN from_col, to_col, size(shared) AS overlap_count
    ORDER BY overlap_count DESC
    LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"from_fqn": from_fqn, "to_fqn": to_fqn})
    logger.debug(
        "neo4j | fn=find_join_by_value_overlap | from={} to={} | ms={:.0f} | hits={}",
        from_fqn, to_fqn, (time.monotonic() - t0) * 1000, len(results),
    )
    return [dict(r) for r in results]


@neo4j_breaker
def resolve_columns(table_fqn: str, column_names: list[str]) -> list[dict]:
    """Direct column lookup by table + name list. Used as last-resort in filter_resolver."""
    query = """
    MATCH (t:Table {fqn: $table_fqn})-[:HAS_COLUMN]->(c:Column)
    WHERE c.name IN $column_names
    RETURN c.name AS name, c.table_fqn AS table_fqn, c.data_type AS data_type,
           c.semantic_type AS semantic_type,
           c.sample_values AS sample_values,
           c.top_freq_values AS top_freq_values, c.value_aliases AS value_aliases,
           c.value_vocabulary AS value_vocabulary,
           c.description AS description, c.synonyms AS synonyms
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"table_fqn": table_fqn, "column_names": column_names})
    logger.debug("neo4j | fn=resolve_columns | table={} | ms={:.0f} | cols={}", table_fqn, (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]

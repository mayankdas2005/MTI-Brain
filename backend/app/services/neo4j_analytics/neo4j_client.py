"""Neo4j client for the analytics pipeline.

One function per Cypher query. Each function logs timing and result count.
All queries use explicit parameters — never string interpolation.
"""

from __future__ import annotations

import time

from app.core.config import settings
from app.core.logger import logger

_driver = None


def init_neo4j() -> None:
    """Initialize the Neo4j driver and bootstrap required vector indexes."""
    global _driver
    try:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        _driver.verify_connectivity()
        logger.info("Neo4j driver initialized | uri={}", settings.NEO4J_URI)
        _bootstrap_indexes()
    except Exception as e:
        logger.error("Neo4j driver initialization failed: {}", e)
        raise


def close_neo4j() -> None:
    global _driver
    if _driver:
        _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def get_driver():
    if not _driver:
        raise RuntimeError("Neo4j not initialized — call init_neo4j() first.")
    return _driver


def _bootstrap_indexes() -> None:
    """Create vector indexes for QueryPattern and AntiPattern if they don't exist."""
    queries = [
        """CREATE VECTOR INDEX querypattern_cohere_embedding IF NOT EXISTS
           FOR (n:QueryPattern) ON (n.cohere_embedding)
           OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}""",
        """CREATE VECTOR INDEX antipattern_cohere_embedding IF NOT EXISTS
           FOR (n:AntiPattern) ON (n.cohere_embedding)
           OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}""",
    ]
    with get_driver().session() as session:
        for q in queries:
            try:
                session.run(q)
            except Exception as e:
                logger.warning("Index bootstrap warning (non-fatal): {}", e)
    logger.info("Neo4j vector indexes bootstrapped")


# ── Template search ──────────────────────────────────────────────────────────

def search_query_templates(embedding: list[float]) -> list[dict]:
    query = """
    CALL db.index.vector.queryNodes('querytemplate_cohere_embedding', 5, $embedding)
    YIELD node AS qt, score
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent, qt.anchor_table_fqns AS anchor_table_fqns,
           qt.cte_steps AS cte_steps, qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters, qt.complexity AS complexity,
           score
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, embedding=embedding, timeout=10))
    logger.debug("neo4j | fn=search_query_templates | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Table search ─────────────────────────────────────────────────────────────

def search_tables_fulltext(query_text: str) -> list[dict]:
    query = """
    CALL db.index.fulltext.queryNodes('Table_name_description_synonyms_text_intent_tags_text', $query)
    YIELD node AS t, score
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           score LIMIT 10
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, query=query_text, timeout=10))
    logger.debug("neo4j | fn=search_tables_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Column search ─────────────────────────────────────────────────────────────

def search_columns_fulltext(query_text: str) -> list[dict]:
    query = """
    CALL db.index.fulltext.queryNodes('Column_name_description_synonyms_text_top_values_text', $query)
    YIELD node AS c, score
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.semantic_type AS semantic_type, c.default_aggregation AS default_aggregation,
           c.is_measurable AS is_measurable, c.is_groupable AS is_groupable,
           score LIMIT 15
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, query=query_text, timeout=10))
    logger.debug("neo4j | fn=search_columns_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── BusinessTerm lookup ──────────────────────────────────────────────────────

def lookup_business_terms(tokens: list[str]) -> list[dict]:
    query = """
    MATCH (bt:BusinessTerm)
    WHERE ANY(token IN $tokens WHERE
        toLower(bt.term) = toLower(token)
        OR ANY(v IN bt.variants WHERE toLower(v) = toLower(token)))
    RETURN bt.term AS term, bt.variants AS variants,
           bt.term_type AS term_type, bt.description AS description
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, tokens=tokens, timeout=10))
    logger.debug("neo4j | fn=lookup_business_terms | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Intent search ─────────────────────────────────────────────────────────────

def search_intents(embedding: list[float]) -> list[dict]:
    query = """
    CALL db.index.vector.queryNodes('intent_cohere_embedding', 3, $embedding)
    YIELD node AS i, score
    RETURN i.name AS name, i.description AS description, score
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, embedding=embedding, timeout=10))
    logger.debug("neo4j | fn=search_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── JoinPath loading ─────────────────────────────────────────────────────────

def load_join_path(from_fqn: str, to_fqn: str) -> dict | None:
    query = """
    MATCH (jp:JoinPath)
    WHERE jp.from_fqn = $from AND jp.to_fqn = $to
      AND jp.algorithm = 'dijkstra' AND jp.k_rank = 1
    RETURN jp.id AS id, jp.join_clauses AS join_clauses, jp.path_tables AS path_tables,
           jp.hop_count AS hop_count, jp.total_cost AS total_cost,
           jp.quality_score AS quality_score, jp.is_cross_community AS is_cross_community
    LIMIT 1
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        result = session.run(query, **{"from": from_fqn, "to": to_fqn}, timeout=10).single()
    logger.debug("neo4j | fn=load_join_path | from={} to={} | ms={:.0f} | found={}", from_fqn, to_fqn, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


def load_join_path_yens(from_fqn: str, to_fqn: str) -> dict | None:
    """Fallback: try Yen's k=1 path when Dijkstra k=1 is missing."""
    query = """
    MATCH (jp:JoinPath)
    WHERE jp.from_fqn = $from AND jp.to_fqn = $to
      AND jp.algorithm = 'yens' AND jp.k_rank = 1
    RETURN jp.id AS id, jp.join_clauses AS join_clauses, jp.path_tables AS path_tables,
           jp.hop_count AS hop_count, jp.total_cost AS total_cost,
           jp.quality_score AS quality_score, jp.is_cross_community AS is_cross_community
    LIMIT 1
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        result = session.run(query, **{"from": from_fqn, "to": to_fqn}, timeout=10).single()
    logger.debug("neo4j | fn=load_join_path_yens | from={} to={} | ms={:.0f} | found={}", from_fqn, to_fqn, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


def write_join_path(path_data: dict) -> None:
    """Write a newly computed JoinPath back to Neo4j for future reuse."""
    query = """
    MERGE (jp:JoinPath {id: $id})
    SET jp += $props
    """
    with get_driver().session() as session:
        session.run(query, id=path_data["id"], props=path_data)
    logger.debug("neo4j | fn=write_join_path | id={}", path_data.get("id"))


# ── Column resolution ────────────────────────────────────────────────────────

def resolve_columns(table_fqn: str, column_names: list[str]) -> list[dict]:
    query = """
    MATCH (t:Table {fqn: $table_fqn})-[:HAS_COLUMN]->(c:Column)
    WHERE c.name IN $column_names
    RETURN c.name AS name, c.table_fqn AS table_fqn, c.data_type AS data_type,
           c.semantic_type AS semantic_type, c.default_aggregation AS default_aggregation,
           c.temporal_grain AS temporal_grain, c.sample_values AS sample_values,
           c.top_freq_values AS top_freq_values, c.value_aliases AS value_aliases,
           c.filter_selectivity AS filter_selectivity, c.value_vocabulary AS value_vocabulary
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, table_fqn=table_fqn, column_names=column_names, timeout=10))
    logger.debug("neo4j | fn=resolve_columns | table={} | ms={:.0f} | cols={}", table_fqn, (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── QueryPattern / AntiPattern ───────────────────────────────────────────────

def search_query_patterns(embedding: list[float]) -> list[dict]:
    query = """
    CALL db.index.vector.queryNodes('querypattern_cohere_embedding', 2, $embedding)
    YIELD node AS qp, score WHERE score > 0.75
    RETURN qp.sql_cte_outline AS sql_cte_outline, qp.tables_used AS tables_used,
           qp.intent AS intent, score
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, embedding=embedding, timeout=10))
    logger.debug("neo4j | fn=search_query_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


def search_anti_patterns(embedding: list[float]) -> list[dict]:
    query = """
    CALL db.index.vector.queryNodes('antipattern_cohere_embedding', 2, $embedding)
    YIELD node AS ap, score WHERE score > 0.75
    RETURN ap.error_type AS error_type, ap.error_summary AS error_summary,
           ap.sql_fragment AS sql_fragment, score
    """
    t0 = time.monotonic()
    with get_driver().session() as session:
        results = list(session.run(query, embedding=embedding, timeout=10))
    logger.debug("neo4j | fn=search_anti_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


def write_query_pattern(pattern_data: dict) -> None:
    query = """
    MERGE (qp:QueryPattern {id: $id})
    SET qp += $props
    """
    with get_driver().session() as session:
        session.run(query, id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_query_pattern | id={}", pattern_data.get("id"))


def write_anti_pattern(pattern_data: dict) -> None:
    query = """
    MERGE (ap:AntiPattern {id: $id})
    SET ap += $props
    """
    with get_driver().session() as session:
        session.run(query, id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_anti_pattern | id={}", pattern_data.get("id"))

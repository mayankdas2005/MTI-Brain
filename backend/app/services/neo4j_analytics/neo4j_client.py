"""Neo4j client for the analytics pipeline.

One function per Cypher query. Each function logs timing and result count.
All queries use explicit parameters — never string interpolation.
"""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
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
            max_connection_pool_size=settings.NEO4J_MAX_POOL_SIZE,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            connection_acquisition_timeout=settings.NEO4J_ACQUISITION_TIMEOUT,
        )
        _driver.verify_connectivity()
        with _driver.session(database=settings.NEO4J_DB) as _s:
            _s.run("RETURN 1").consume()
        logger.info(
            "Neo4j driver initialized | uri={} | pool={}",
            settings.NEO4J_URI, settings.NEO4J_MAX_POOL_SIZE,
        )
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
    """Create vector indexes for QueryPattern, AntiPattern, Table, and Column if they don't exist."""
    queries = [
        """CREATE VECTOR INDEX querypattern_cohere_embedding IF NOT EXISTS
           FOR (n:QueryPattern) ON (n.cohere_embedding)
           OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}""",
        """CREATE VECTOR INDEX antipattern_cohere_embedding IF NOT EXISTS
           FOR (n:AntiPattern) ON (n.cohere_embedding)
           OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}""",
        """CREATE FULLTEXT INDEX businessterm_ft IF NOT EXISTS
           FOR (n:BusinessTerm) ON EACH [n.term, n.variants, n.description]""",
    ]
    with get_driver().session(database=settings.NEO4J_DB) as session:
        for q in queries:
            try:
                session.run(q)
            except Exception as e:
                logger.warning("Index bootstrap warning (non-fatal): {}", e)
    logger.info("Neo4j vector indexes bootstrapped")


# ── Template search ──────────────────────────────────────────────────────────

@neo4j_breaker
def search_query_templates(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (qt:QueryTemplate)
    SEARCH qt IN (
      VECTOR INDEX `querytemplate_cohere` FOR $embedding
      LIMIT 5
    )
    SCORE AS score
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent, qt.anchor_table_fqns AS anchor_table_fqns,
           qt.cte_steps AS cte_steps, qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters, qt.complexity AS complexity,
           score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_query_templates | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Table search ─────────────────────────────────────────────────────────────

@neo4j_breaker
def search_tables_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (t:Table)
    SEARCH t IN (
      VECTOR INDEX `tbl_cohere_embedding` FOR $embedding
      LIMIT 10
    )
    SCORE AS score
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_tables_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('table_ft_extended', $query)
    YIELD node AS t, score
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           score LIMIT 10
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(cypher, {"query": query_text}))
    logger.debug("neo4j | fn=search_tables_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Column search ─────────────────────────────────────────────────────────────

@neo4j_breaker
def search_columns_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (c:Column)
    SEARCH c IN (
      VECTOR INDEX `col_cohere_embedding` FOR $embedding
      LIMIT 15
    )
    SCORE AS score
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.semantic_type AS semantic_type, c.default_aggregation AS default_aggregation,
           c.is_measurable AS is_measurable, c.is_groupable AS is_groupable,
           c.data_type AS data_type, c.filter_selectivity AS filter_selectivity,
           score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_columns_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_columns_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('col_ft_extended', $query)
    YIELD node AS c, score
    RETURN c.id AS id, c.name AS name, c.table_fqn AS table_fqn,
           c.semantic_type AS semantic_type, c.default_aggregation AS default_aggregation,
           c.is_measurable AS is_measurable, c.is_groupable AS is_groupable,
           score LIMIT 15
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(cypher, {"query": query_text}))
    logger.debug("neo4j | fn=search_columns_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── QueryTemplate fulltext ───────────────────────────────────────────────────

@neo4j_breaker
def search_query_templates_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('querytemplate_ft', $query)
    YIELD node AS qt, score
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent, qt.anchor_table_fqns AS anchor_table_fqns,
           qt.cte_steps AS cte_steps, qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters, qt.complexity AS complexity,
           qt.description AS description,
           score LIMIT 5
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(cypher, {"query": query_text}))
    logger.debug("neo4j | fn=search_query_templates_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Table traversal paths ────────────────────────────────────────────────────

@neo4j_breaker
def search_tables_via_templates_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (qt:QueryTemplate)
    SEARCH qt IN (
      VECTOR INDEX `querytemplate_cohere` FOR $embedding
      LIMIT 5
    )
    SCORE AS template_score
    WITH qt, template_score
    MATCH (qt)-[:REQUIRES_TABLE]->(t:Table)
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           template_score AS score, qt.id AS matched_via
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_tables_via_templates_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_templates_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('querytemplate_ft', $query)
    YIELD node AS qt, score AS template_score
    WITH qt, template_score
    MATCH (qt)-[:REQUIRES_TABLE]->(t:Table)
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           template_score AS score, qt.id AS matched_via
    LIMIT 20
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(cypher, {"query": query_text}))
    logger.debug("neo4j | fn=search_tables_via_templates_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_intents(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (i:Intent)
    SEARCH i IN (
      VECTOR INDEX `intent_cohere` FOR $embedding
      LIMIT 3
    )
    SCORE AS intent_score
    WITH i, intent_score
    MATCH (t:Table)-[:RELEVANT_TO]->(i)
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           intent_score AS score, i.name AS matched_via
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_tables_via_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_community(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (c:Community)
    SEARCH c IN (
      VECTOR INDEX `community_cohere` FOR $embedding
      LIMIT 2
    )
    SCORE AS community_score
    WITH c, community_score
    MATCH (c)-[:CONTAINS_TABLE]->(t:Table)
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           community_score AS score, c.dominant_domain AS matched_via
    ORDER BY community_score DESC
    LIMIT 15
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_tables_via_community | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_domain(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (d:Domain)
    SEARCH d IN (
      VECTOR INDEX `domain_cohere` FOR $embedding
      LIMIT 2
    )
    SCORE AS domain_score
    WITH d, domain_score
    MATCH (t:Table)-[:BELONGS_TO]->(d)
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           domain_score AS score, d.name AS matched_via
    ORDER BY domain_score DESC
    LIMIT 20
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_tables_via_domain | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def get_columns_for_tables(table_fqns: list[str]) -> list[dict]:
    if not table_fqns:
        return []
    query = """
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE t.fqn IN $table_fqns
    RETURN c.name AS name, c.table_fqn AS table_fqn,
           c.data_type AS data_type,
           c.semantic_type AS semantic_type,
           c.default_aggregation AS default_aggregation,
           c.description AS description,
           c.is_measurable AS is_measurable,
           c.is_groupable AS is_groupable,
           c.filter_selectivity AS filter_selectivity,
           c.sample_values AS sample_values,
           c.value_vocabulary AS value_vocabulary,
           c.value_aliases AS value_aliases,
           c.value_scale AS value_scale,
           c.synonyms AS synonyms
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"table_fqns": table_fqns}))
    logger.debug("neo4j | fn=get_columns_for_tables | ms={:.0f} | cols={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_business_terms_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (bt:BusinessTerm)
    SEARCH bt IN (
      VECTOR INDEX `businessterm_cohere` FOR $embedding
      LIMIT 5
    )
    SCORE AS score
    WHERE score > 0.70
    RETURN bt.term AS term, bt.variants AS variants,
           bt.term_type AS term_type, bt.description AS description, score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_business_terms_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_joinpaths(candidate_fqns: list[str]) -> list[dict]:
    if not candidate_fqns:
        return []
    query = """
    MATCH (jp:JoinPath)
    WHERE (jp.from_fqn IN $fqns OR jp.to_fqn IN $fqns)
      AND jp.quality_score >= 0.8
      AND jp.hop_count <= 1
    WITH jp,
         CASE WHEN jp.from_fqn IN $fqns THEN jp.to_fqn ELSE jp.from_fqn END AS target_fqn,
         CASE jp.algorithm WHEN 'dijkstra' THEN 0 ELSE jp.k_rank END AS path_priority
    WHERE NOT target_fqn IN $fqns
    ORDER BY path_priority ASC, jp.quality_score DESC
    WITH target_fqn, COLLECT(jp)[0] AS best_jp
    MATCH (t:Table {fqn: target_fqn})
    RETURN t.fqn AS fqn, t.name AS name, t.description AS description,
           t.grain AS grain, t.synonyms AS synonyms,
           t.business_domain AS business_domain, t.community_id AS community_id,
           t.typical_join_role AS typical_join_role, t.table_type AS table_type,
           t.is_time_series AS is_time_series,
           t.natural_dimensions AS natural_dimensions,
           t.natural_measures AS natural_measures,
           best_jp.quality_score AS score,
           (best_jp.from_fqn + ' -> ' + best_jp.to_fqn) AS matched_via
    ORDER BY best_jp.quality_score DESC
    LIMIT 10
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"fqns": candidate_fqns}))
    logger.debug("neo4j | fn=search_tables_via_joinpaths | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_business_terms_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('businessterm_ft', $query)
    YIELD node AS bt, score
    RETURN bt.term AS term, bt.variants AS variants,
           bt.term_type AS term_type, bt.description AS description,
           score LIMIT 5
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(cypher, {"query": query_text}))
    logger.debug("neo4j | fn=search_business_terms_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── BusinessTerm lookup ──────────────────────────────────────────────────────

@neo4j_breaker
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
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"tokens": tokens}))
    logger.debug("neo4j | fn=lookup_business_terms | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Intent search ─────────────────────────────────────────────────────────────

@neo4j_breaker
def search_intents(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (i:Intent)
    SEARCH i IN (
      VECTOR INDEX `intent_cohere` FOR $embedding
      LIMIT 3
    )
    SCORE AS score
    RETURN i.name AS name, i.description AS description, score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── JoinPath loading ─────────────────────────────────────────────────────────

@neo4j_breaker
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
    with get_driver().session(database=settings.NEO4J_DB) as session:
        result = session.run(query, {"from": from_fqn, "to": to_fqn}).single()
    logger.debug("neo4j | fn=load_join_path | from={} to={} | ms={:.0f} | found={}", from_fqn, to_fqn, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


@neo4j_breaker
def load_join_path_yens(from_fqn: str, to_fqn: str, k_rank: int = 1) -> dict | None:
    """Fallback: try Yen's k-shortest path for the given k_rank (1, 2, or 3)."""
    query = """
    MATCH (jp:JoinPath)
    WHERE jp.from_fqn = $from AND jp.to_fqn = $to
      AND jp.algorithm = 'yens' AND jp.k_rank = $k_rank
    RETURN jp.id AS id, jp.join_clauses AS join_clauses, jp.path_tables AS path_tables,
           jp.hop_count AS hop_count, jp.total_cost AS total_cost,
           jp.quality_score AS quality_score, jp.is_cross_community AS is_cross_community
    LIMIT 1
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        result = session.run(query, {"from": from_fqn, "to": to_fqn, "k_rank": k_rank}).single()
    logger.debug("neo4j | fn=load_join_path_yens | from={} to={} | k={} | ms={:.0f} | found={}", from_fqn, to_fqn, k_rank, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


def load_best_join_path(from_fqn: str, to_fqn: str) -> dict | None:
    """Load the best available JoinPath using a fallback sequence.

    Tries in order: Dijkstra k=1 → Yen's k=1 → Yen's k=2 → Yen's k=3.
    Returns None only if no precomputed path exists at all.
    """
    result = load_join_path(from_fqn, to_fqn)
    if result:
        return result

    for k in (1, 2, 3):
        result = load_join_path_yens(from_fqn, to_fqn, k_rank=k)
        if result:
            logger.info("neo4j | join_path fallback | from={} to={} | yens k={}", from_fqn, to_fqn, k)
            return result

    logger.warning("neo4j | no join_path found | from={} to={}", from_fqn, to_fqn)
    return None


@neo4j_breaker
def write_join_path(path_data: dict) -> None:
    """Write a newly computed JoinPath back to Neo4j for future reuse."""
    query = """
    MERGE (jp:JoinPath {id: $id})
    SET jp += $props
    """
    with get_driver().session(database=settings.NEO4J_DB) as session:
        session.run(query, id=path_data["id"], props=path_data)
    logger.debug("neo4j | fn=write_join_path | id={}", path_data.get("id"))


# ── Column resolution ────────────────────────────────────────────────────────

@neo4j_breaker
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
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"table_fqn": table_fqn, "column_names": column_names}))
    logger.debug("neo4j | fn=resolve_columns | table={} | ms={:.0f} | cols={}", table_fqn, (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── QueryPattern / AntiPattern ───────────────────────────────────────────────

@neo4j_breaker
def search_query_patterns(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (qp:QueryPattern)
    SEARCH qp IN (
      VECTOR INDEX `querypattern_cohere_embedding` FOR $embedding
      LIMIT 2
    )
    SCORE AS score
    WHERE score > 0.75
    RETURN qp.sql_cte_outline AS sql_cte_outline, qp.tables_used AS tables_used,
           qp.intent AS intent, score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_query_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_anti_patterns(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (ap:AntiPattern)
    SEARCH ap IN (
      VECTOR INDEX `antipattern_cohere_embedding` FOR $embedding
      LIMIT 2
    )
    SCORE AS score
    WHERE score > 0.75
    RETURN ap.error_type AS error_type, ap.error_summary AS error_summary,
           ap.sql_fragment AS sql_fragment, score
    """
    t0 = time.monotonic()
    with get_driver().session(database=settings.NEO4J_DB) as session:
        results = list(session.run(query, {"embedding": embedding}))
    logger.debug("neo4j | fn=search_anti_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def write_query_pattern(pattern_data: dict) -> None:
    query = """
    MERGE (qp:QueryPattern {id: $id})
    SET qp += $props
    """
    with get_driver().session(database=settings.NEO4J_DB) as session:
        session.run(query, id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_query_pattern | id={}", pattern_data.get("id"))


@neo4j_breaker
def write_anti_pattern(pattern_data: dict) -> None:
    query = """
    MERGE (ap:AntiPattern {id: $id})
    SET ap += $props
    """
    with get_driver().session(database=settings.NEO4J_DB) as session:
        session.run(query, id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_anti_pattern | id={}", pattern_data.get("id"))

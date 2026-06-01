"""Neo4j client for the analytics pipeline.

One function per Cypher query. Each function logs timing and result count.
All queries use explicit parameters — never string interpolation.
"""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.config import settings
from app.core.logger import logger
from app.core.retry import retry_sync

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
            max_connection_lifetime=300,
            keep_alive=True,
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


def _neo4j_run(cypher: str, params: dict) -> list:
    """Execute a Cypher query, retrying on defunct-connection / transient errors.

    Opens a fresh driver session on each attempt so a stale pooled connection
    cannot be reused across retries. The @neo4j_breaker on the calling function
    sees only one failure if all retries are exhausted.
    """
    return retry_sync(
        lambda: _do_neo4j_run(cypher, params),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_run(cypher: str, params: dict) -> list:
    with get_driver().session(database=settings.NEO4J_DB) as session:
        return list(session.run(cypher, params))


def _neo4j_run_single(cypher: str, params: dict):
    """Like _neo4j_run but returns a single Record (or None) with retry."""
    return retry_sync(
        lambda: _do_neo4j_run_single(cypher, params),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_run_single(cypher: str, params: dict):
    with get_driver().session(database=settings.NEO4J_DB) as session:
        return session.run(cypher, params).single()


def _neo4j_write(cypher: str, **kwargs) -> None:
    """Execute a write Cypher query with retry."""
    retry_sync(
        lambda: _do_neo4j_write(cypher, **kwargs),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_write(cypher: str, **kwargs) -> None:
    with get_driver().session(database=settings.NEO4J_DB) as session:
        session.run(cypher, **kwargs)


def _bootstrap_indexes() -> None:
    """Create all required indexes and constraints on backend startup.

    QueryPattern and AntiPattern vector indexes are NOT created here because
    those nodes have no data yet — bootstrapping on an empty schema causes
    Neo4j 01N52 property-key warnings on every query. Those indexes are created
    lazily by write_query_pattern / write_anti_pattern.

    SCHEMA_DDL is the single source of truth in neo4j_writer.py.
    All DDL statements are idempotent (IF NOT EXISTS).
    """
    try:
        from semantic_model_generator.graph.load.neo4j_writer import SCHEMA_DDL, FULLTEXT_DDL
        queries = SCHEMA_DDL + FULLTEXT_DDL
    except ImportError:
        # Fallback: minimal set if semantic_model_generator not installed
        queries = [
            "CREATE CONSTRAINT tbl_fqn_unique IF NOT EXISTS FOR (t:Table) REQUIRE t.fqn IS UNIQUE",
            "CREATE CONSTRAINT col_id_unique IF NOT EXISTS FOR (c:Column) REQUIRE c.id IS UNIQUE",
        ]

    with get_driver().session(database=settings.NEO4J_DB) as session:
        for q in queries:
            try:
                session.run(q)
            except Exception as e:
                logger.warning("Index bootstrap warning (non-fatal): {}", e)
    logger.info("Neo4j schema indexes bootstrapped")


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
    WHERE coalesce(qt.template_confidence, 0) >= 0.6
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent,
           qt.anchor_table_fqns_resolved AS anchor_table_fqns,
           qt.cte_steps AS cte_steps, qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters, qt.complexity AS complexity,
           qt.sql_pattern AS sql_pattern, qt.is_cross_domain AS is_cross_domain,
           qt.template_confidence AS template_confidence, qt.is_validated AS is_validated,
           score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(cypher, {"query": query_text})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(cypher, {"query": query_text})
    logger.debug("neo4j | fn=search_columns_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── QueryTemplate fulltext ───────────────────────────────────────────────────

@neo4j_breaker
def search_query_templates_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('querytemplate_ft', $query)
    YIELD node AS qt, score
    WHERE coalesce(qt.template_confidence, 0) >= 0.6
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent,
           qt.anchor_table_fqns_resolved AS anchor_table_fqns,
           qt.cte_steps AS cte_steps, qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters, qt.complexity AS complexity,
           qt.description AS description, qt.sql_pattern AS sql_pattern,
           qt.is_cross_domain AS is_cross_domain, qt.template_confidence AS template_confidence,
           score LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"query": query_text})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(cypher, {"query": query_text})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    results = _neo4j_run(query, {"table_fqns": table_fqns})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": candidate_fqns})
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
    results = _neo4j_run(cypher, {"query": query_text})
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
    results = _neo4j_run(query, {"tokens": tokens})
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
    results = _neo4j_run(query, {"embedding": embedding})
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
    result = _neo4j_run_single(query, {"from": from_fqn, "to": to_fqn})
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
    result = _neo4j_run_single(query, {"from": from_fqn, "to": to_fqn, "k_rank": k_rank})
    logger.debug("neo4j | fn=load_join_path_yens | from={} to={} | k={} | ms={:.0f} | found={}", from_fqn, to_fqn, k_rank, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


@neo4j_breaker
def load_join_path_dijkstra(from_fqn: str, to_fqn: str, k_rank: int = 1) -> dict | None:
    """Fetch a pre-computed dijkstra JoinPath for the given k_rank (1, 2, or 3)."""
    query = """
    MATCH (jp:JoinPath)
    WHERE jp.from_fqn = $from AND jp.to_fqn = $to
      AND jp.algorithm = 'dijkstra' AND jp.k_rank = $k_rank
    RETURN jp.id AS id, jp.join_clauses AS join_clauses, jp.path_tables AS path_tables,
           jp.hop_count AS hop_count, jp.total_cost AS total_cost,
           jp.quality_score AS quality_score, jp.is_cross_community AS is_cross_community
    LIMIT 1
    """
    t0 = time.monotonic()
    result = _neo4j_run_single(query, {"from": from_fqn, "to": to_fqn, "k_rank": k_rank})
    logger.debug("neo4j | fn=load_join_path_dijkstra | from={} to={} | k={} | ms={:.0f} | found={}", from_fqn, to_fqn, k_rank, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


def collect_all_join_paths(from_fqn: str, to_fqn: str) -> list[dict]:
    """Collect ALL available join paths between two tables — no early exit.

    Returns paths from every tier and k-rank in priority order:
      1. JOINS_TO direct FK edges (forward + reverse)
      2. dijkstra k=1,2,3 forward
      3. dijkstra k=1,2,3 reverse
      4. yens k=1,2,3 forward
      5. yens k=1,2,3 reverse

    Each result is tagged with 'tier' and 'direction'.
    Deduplicates by normalized join_clauses content.
    load_best_join_path is unchanged and still used when a single best path is needed.
    """
    paths: list[dict] = []
    seen_clauses: set[str] = set()

    def _dedup_key(p: dict) -> str:
        return "|".join(sorted(str(c) for c in (p.get("join_clauses") or [])))

    def _add(path: dict | None, tier: str, direction: str = "forward") -> None:
        if not path:
            return
        key = _dedup_key(path)
        if key and key not in seen_clauses:
            seen_clauses.add(key)
            paths.append({**path, "tier": tier, "direction": direction})

    # ── JOINS_TO direct FK edges ──────────────────────────────────────────────
    try:
        edges = get_direct_joins([from_fqn, to_fqn])
        for e in edges:
            f, t = e.get("from_fqn"), e.get("to_fqn")
            fc, tc = e.get("from_col"), e.get("to_col")
            if not (f and t and fc and tc):
                continue
            direction = "forward" if f == from_fqn else "reverse"
            clause = f"{f}.{fc} = {t}.{tc}"
            key = clause
            if key not in seen_clauses:
                seen_clauses.add(key)
                paths.append({
                    "id": "",
                    "join_clauses": [clause],
                    "path_tables": [f, t],
                    "hop_count": 1,
                    "total_cost": None,
                    "quality_score": None,
                    "is_cross_community": False,
                    "tier": "joins_to",
                    "direction": direction,
                })
    except Exception as exc:
        logger.warning("neo4j | collect_all_join_paths | JOINS_TO failed | from={} to={} | error={}", from_fqn, to_fqn, exc)

    # ── dijkstra k=1,2,3 forward + reverse ───────────────────────────────────
    for k in (1, 2, 3):
        _add(load_join_path_dijkstra(from_fqn, to_fqn, k_rank=k), tier=f"dijkstra_k{k}", direction="forward")
        _add(load_join_path_dijkstra(to_fqn, from_fqn, k_rank=k), tier=f"dijkstra_k{k}", direction="reverse")

    # ── yens k=1,2,3 forward + reverse ───────────────────────────────────────
    for k in (1, 2, 3):
        _add(load_join_path_yens(from_fqn, to_fqn, k_rank=k), tier=f"yens_k{k}", direction="forward")
        _add(load_join_path_yens(to_fqn, from_fqn, k_rank=k), tier=f"yens_k{k}", direction="reverse")

    logger.info("neo4j | collect_all_join_paths | from={} to={} | total_paths={}", from_fqn, to_fqn, len(paths))
    return paths


def load_best_join_path(from_fqn: str, to_fqn: str) -> dict | None:
    """Load the best available join path using a three-tier cascade.

    Tier 1 — JOINS_TO direct edge  : authoritative FK relationship, found immediately
    Tier 2 — JoinPath dijkstra k=1 : forward then reversed
    Tier 3 — JoinPath yens k=1,2,3 : alternative multi-hop paths

    Returns None only when all tiers fail.
    """
    # ── Tier 1: JOINS_TO direct FK edge (authoritative — try first) ──────────
    try:
        edges = get_direct_joins([from_fqn, to_fqn])
        for e in edges:
            f, t = e.get("from_fqn"), e.get("to_fqn")
            fc, tc = e.get("from_col"), e.get("to_col")
            if not (f and t and fc and tc):
                continue
            if (f == from_fqn and t == to_fqn) or (f == to_fqn and t == from_fqn):
                clause = f"{f}.{fc} = {t}.{tc}"
                logger.info(
                    "neo4j | join via JOINS_TO (tier1) | from={} to={} | clause={} | confidence={}",
                    from_fqn, to_fqn, clause, e.get("confidence"),
                )
                return {
                    "id": "",
                    "join_clauses": [clause],
                    "path_tables": [f, t],
                    "hop_count": 1,
                    "total_cost": None,
                    "quality_score": None,
                    "is_cross_community": False,
                    "tier": "joins_to",
                }
    except Exception as exc:
        logger.warning("neo4j | JOINS_TO edge lookup failed | from={} to={} | error={}", from_fqn, to_fqn, exc)

    # ── Tier 2: JoinPath dijkstra k=1 (forward then reversed) ────────────────
    result = load_join_path(from_fqn, to_fqn)
    if result:
        logger.info("neo4j | join via JoinPath dijkstra (tier2) | from={} to={}", from_fqn, to_fqn)
        result["tier"] = "dijkstra"
        return result

    result = load_join_path(to_fqn, from_fqn)
    if result:
        logger.info("neo4j | join via JoinPath dijkstra reversed (tier2) | from={} to={}", from_fqn, to_fqn)
        result["tier"] = "dijkstra_reversed"
        return result

    # ── Tier 3: JoinPath yens k=1,2,3 ────────────────────────────────────────
    for k in (1, 2, 3):
        result = load_join_path_yens(from_fqn, to_fqn, k_rank=k)
        if result:
            logger.info("neo4j | join via JoinPath yens k={} (tier3) | from={} to={}", k, from_fqn, to_fqn)
            result["tier"] = f"yens_k{k}"
            return result

    logger.warning("neo4j | NO join found (tried JOINS_TO + dijkstra + yens) | from={} to={}", from_fqn, to_fqn)
    return None


@neo4j_breaker
def get_direct_joins(table_fqns: list[str]) -> list[dict]:
    """Batch query JOINS_TO edges between the given tables.

    Single round-trip: returns all direct join edges where both endpoints are in table_fqns.
    Each row carries from_fqn, to_fqn, from_col, to_col, join_type, confidence.
    """
    if not table_fqns:
        return []
    query = """
    MATCH (t1:Table)-[r:JOINS_TO]->(t2:Table)
    WHERE t1.fqn IN $fqns AND t2.fqn IN $fqns AND t1.fqn <> t2.fqn
    RETURN t1.fqn AS from_fqn, t2.fqn AS to_fqn,
           r.from_col AS from_col, r.to_col AS to_col,
           r.recommended_join_type AS join_type,
           r.confidence AS confidence
    ORDER BY r.confidence DESC
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": table_fqns})
    logger.debug("neo4j | fn=get_direct_joins | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def get_tables_with_context(table_fqns: list[str]) -> list[dict]:
    """Fetch Table nodes with their Domain and Community associations.

    Returns rows with keys: t (table props), d (domain props or None), c (community props or None).
    """
    if not table_fqns:
        return []
    query = """
    MATCH (t:Table) WHERE t.fqn IN $fqns
    OPTIONAL MATCH (t)-[:BELONGS_TO]->(d:Domain)
    OPTIONAL MATCH (c:Community)-[:CONTAINS_TABLE]->(t)
    RETURN properties(t) AS t, properties(d) AS d, properties(c) AS c
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": table_fqns})
    logger.debug("neo4j | fn=get_tables_with_context | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [{"t": dict(r["t"] or {}), "d": dict(r["d"]) if r["d"] else None, "c": dict(r["c"]) if r["c"] else None} for r in results]


@neo4j_breaker
def get_table_relevant_intents(table_fqns: list[str]) -> list[dict]:
    """Fetch Table -[RELEVANT_TO]-> Intent edges for the given tables."""
    if not table_fqns:
        return []
    query = """
    MATCH (t:Table)-[r:RELEVANT_TO]->(i:Intent)
    WHERE t.fqn IN $fqns
    RETURN t.fqn AS table_fqn, properties(i) AS intent, properties(r) AS rel
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": table_fqns})
    logger.debug("neo4j | fn=get_table_relevant_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [{"table_fqn": r["table_fqn"], "intent": dict(r["intent"] or {}), "rel": dict(r["rel"] or {})} for r in results]


@neo4j_breaker
def get_structurally_similar_tables(table_fqns: list[str]) -> list[dict]:
    """Fetch Table -[STRUCTURALLY_SIMILAR]-> Table edges within the given table set."""
    if not table_fqns:
        return []
    query = """
    MATCH (t1:Table)-[r:STRUCTURALLY_SIMILAR]->(t2:Table)
    WHERE t1.fqn IN $fqns AND t2.fqn IN $fqns
    RETURN t1.fqn AS from_fqn, t2.fqn AS to_fqn, properties(r) AS rel
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": table_fqns})
    logger.debug("neo4j | fn=get_structurally_similar_tables | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [{"from_fqn": r["from_fqn"], "to_fqn": r["to_fqn"], "rel": dict(r["rel"] or {})} for r in results]


@neo4j_breaker
def get_semantically_similar_columns(table_fqns: list[str]) -> list[dict]:
    """Fetch Column -[SEMANTICALLY_SIMILAR]-> Column edges for columns in the given tables."""
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
def get_community_bridges(community_ids: list[str]) -> list[dict]:
    """Fetch Community -[BRIDGES_TO]-> Community edges within the given community set."""
    if not community_ids:
        return []
    query = """
    MATCH (c1:Community)-[r:BRIDGES_TO]->(c2:Community)
    WHERE c1.id IN $ids AND c2.id IN $ids
    RETURN c1.id AS from_id, c2.id AS to_id, properties(r) AS rel
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": community_ids})
    logger.debug("neo4j | fn=get_community_bridges | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [{"from_id": r["from_id"], "to_id": r["to_id"], "rel": dict(r["rel"] or {})} for r in results]


@neo4j_breaker
def get_join_paths_by_ids(path_ids: list[str]) -> list[dict]:
    """Fetch JoinPath nodes by their IDs."""
    if not path_ids:
        return []
    query = """
    MATCH (jp:JoinPath) WHERE jp.id IN $ids
    RETURN properties(jp) AS jp
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": path_ids})
    logger.debug("neo4j | fn=get_join_paths_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["jp"] or {}) for r in results]


@neo4j_breaker
def get_business_terms_by_terms(terms: list[str]) -> list[dict]:
    """Fetch full BusinessTerm node properties by term name."""
    if not terms:
        return []
    query = """
    MATCH (bt:BusinessTerm) WHERE bt.term IN $terms
    RETURN properties(bt) AS bt
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"terms": terms})
    logger.debug("neo4j | fn=get_business_terms_by_terms | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["bt"] or {}) for r in results]


@neo4j_breaker
def get_query_patterns_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full QueryPattern node properties by ID."""
    if not ids:
        return []
    query = """
    MATCH (qp:QueryPattern) WHERE qp.id IN $ids
    RETURN properties(qp) AS qp
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_query_patterns_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["qp"] or {}) for r in results]


@neo4j_breaker
def get_anti_patterns_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full AntiPattern node properties by ID."""
    if not ids:
        return []
    query = """
    MATCH (ap:AntiPattern) WHERE ap.id IN $ids
    RETURN properties(ap) AS ap
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_anti_patterns_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["ap"] or {}) for r in results]


@neo4j_breaker
def get_query_templates_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full QueryTemplate node properties by ID."""
    if not ids:
        return []
    query = """
    MATCH (qt:QueryTemplate) WHERE qt.id IN $ids
    RETURN properties(qt) AS qt
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_query_templates_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["qt"] or {}) for r in results]


# ── Intent fulltext search ───────────────────────────────────────────────────

@neo4j_breaker
def search_intents_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('intent_ft', $query)
    YIELD node AS i, score
    RETURN i.name AS name, i.description AS description, score
    LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"query": query_text})
    logger.debug("neo4j | fn=search_intents_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── QueryPattern fulltext search ─────────────────────────────────────────────

@neo4j_breaker
def search_query_patterns_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('querypattern_ft', $query)
    YIELD node AS qp, score
    RETURN qp.id AS id, qp.question_text AS question_text, qp.sql_cte_outline AS sql_cte_outline,
           qp.join_outline AS join_outline, qp.filter_summary AS filter_summary,
           qp.tables_used AS tables_used, qp.intent AS intent, qp.complexity AS complexity,
           qp.recompile_count AS recompile_count, qp.repair_count AS repair_count, score
    LIMIT 3
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"query": query_text})
    logger.debug("neo4j | fn=search_query_patterns_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Cross-domain bridge lookup ────────────────────────────────────────────────

@neo4j_breaker
def get_dimension_hub_for_communities(community_ids: list) -> list[dict]:
    """Fetch BRIDGES_TO edges with hub info for a set of community IDs."""
    cypher = """
    MATCH (c1:Community)-[r:BRIDGES_TO]->(c2:Community)
    WHERE c1.id IN $ids AND c2.id IN $ids
      AND r.hub_table_fqn IS NOT NULL
    RETURN c1.id AS from_community, c2.id AS to_community,
           r.hub_table_fqn AS hub_table_fqn, r.hub_join_col AS hub_join_col,
           r.bridge_type AS bridge_type, r.join_safe AS join_safe,
           r.shared_dimension_columns AS shared_dimension_columns
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"ids": community_ids})
    logger.debug("neo4j | fn=get_dimension_hub_for_communities | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


# ── Write delegation ─────────────────────────────────────────────────────────

try:
    from semantic_model_generator.graph.load.neo4j_writer import (
        write_join_path as _wjp,
        write_query_pattern as _wqp,
        write_anti_pattern as _wap,
    )
    _WRITER_AVAILABLE = True
except ImportError:
    _WRITER_AVAILABLE = False


@neo4j_breaker
def write_join_path(path_data: dict) -> None:
    """Write a newly computed JoinPath back to Neo4j for future reuse."""
    if _WRITER_AVAILABLE:
        _wjp(run_fn=_neo4j_write, path_data=path_data)
    else:
        _neo4j_write("MERGE (jp:JoinPath {id: $id}) SET jp += $props",
                     id=path_data["id"], props=path_data)
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
    results = _neo4j_run(query, {"table_fqn": table_fqn, "column_names": column_names})
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
    RETURN qp.id AS id, qp.question_text AS question_text, qp.sql_cte_outline AS sql_cte_outline,
           qp.join_outline AS join_outline, qp.filter_summary AS filter_summary,
           qp.tables_used AS tables_used, qp.intent AS intent, qp.complexity AS complexity,
           qp.recompile_count AS recompile_count, qp.repair_count AS repair_count, score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
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
    RETURN ap.id AS id, ap.error_type AS error_type, ap.error_summary AS error_summary,
           ap.failing_element AS failing_element, ap.complexity AS complexity, score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_anti_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def write_query_pattern(pattern_data: dict) -> None:
    if _WRITER_AVAILABLE:
        _wqp(run_fn=_neo4j_write, pattern_data=pattern_data)
    else:
        _neo4j_write("MERGE (qp:QueryPattern {id: $id}) SET qp += $props",
                     id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_query_pattern | id={}", pattern_data.get("id"))


@neo4j_breaker
def write_anti_pattern(pattern_data: dict) -> None:
    if _WRITER_AVAILABLE:
        _wap(run_fn=_neo4j_write, pattern_data=pattern_data)
    else:
        _neo4j_write("MERGE (ap:AntiPattern {id: $id}) SET ap += $props",
                     id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_anti_pattern | id={}", pattern_data.get("id"))

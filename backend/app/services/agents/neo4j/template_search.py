"""Search functions for QueryTemplate, QueryPattern, AntiPattern, Intent, BusinessTerm."""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_run
from .table_search import _fuzzy_fts


# ── QueryTemplate ─────────────────────────────────────────────────────────────

@neo4j_breaker
def search_query_templates(embedding: list[float]) -> list[dict]:
    """Vector search for QueryTemplates — hints only, never drives table selection."""
    query = """CYPHER 25
    MATCH (qt:QueryTemplate)
    SEARCH qt IN (VECTOR INDEX `querytemplate_cohere` FOR $embedding LIMIT 5)
    SCORE AS score
    WHERE coalesce(qt.template_confidence, 0) >= 0.6
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent,
           qt.anchor_table_fqns_resolved AS anchor_table_fqns,
           qt.cte_steps AS cte_steps,
           qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters,
           qt.complexity AS complexity,
           qt.sql_pattern AS sql_pattern,
           qt.is_cross_domain AS is_cross_domain,
           qt.template_confidence AS template_confidence,
           qt.is_validated AS is_validated,
           score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_query_templates | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_query_templates_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('querytemplate_ft', $query)
    YIELD node AS qt, score
    WHERE coalesce(qt.template_confidence, 0) >= 0.6
    RETURN qt.id AS id, qt.question_text AS question_text,
           qt.primary_intent AS primary_intent,
           qt.anchor_table_fqns_resolved AS anchor_table_fqns,
           qt.cte_steps AS cte_steps,
           qt.required_aggregations AS required_aggregations,
           qt.required_filters AS required_filters,
           qt.complexity AS complexity,
           qt.description AS description,
           qt.sql_pattern AS sql_pattern,
           qt.is_cross_domain AS is_cross_domain,
           qt.template_confidence AS template_confidence,
           score LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"query": _fuzzy_fts(query_text)})
    logger.debug("neo4j | fn=search_query_templates_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def get_query_templates_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    query = "MATCH (qt:QueryTemplate) WHERE qt.id IN $ids RETURN properties(qt) AS qt"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_query_templates_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["qt"] or {}) for r in results]


# ── QueryPattern ──────────────────────────────────────────────────────────────

@neo4j_breaker
def search_query_patterns(embedding: list[float], threshold: float = 0.65, limit: int = 5) -> list[dict]:
    """Vector search for QueryPatterns — ground truth from successful prior queries.

    Returns results ordered by boosted score: raw cosine * frequency bonus * recency bonus.
    Higher occurrence_count and recent last_seen rank above equal-similarity older patterns.
    """
    query = f"""CYPHER 25
    MATCH (qp:QueryPattern)
    SEARCH qp IN (VECTOR INDEX `querypattern_cohere_embedding` FOR $embedding LIMIT {limit})
    SCORE AS score
    WHERE score > $threshold
      AND qp.is_enabled = true
    WITH qp, score,
         score
         * (1.0 + log(1.0 + coalesce(qp.occurrence_count, 1)) * 0.1)
         * CASE WHEN qp.last_seen IS NOT NULL
                  AND duration.between(qp.last_seen, datetime()).days < 30
                THEN 1.1 ELSE 1.0 END
         AS boosted_score
    RETURN qp.id AS id, qp.question_text AS question_text,
           qp.sql_text AS sql_text,
           qp.sql_cte_outline AS sql_cte_outline,
           qp.join_outline AS join_outline,
           qp.filter_summary AS filter_summary,
           qp.measure_summary AS measure_summary,
           qp.dimension_summary AS dimension_summary,
           qp.directive_summary AS directive_summary,
           qp.tables_used AS tables_used,
           qp.intent AS intent, qp.complexity AS complexity,
           qp.recompile_count AS recompile_count,
           qp.repair_count AS repair_count,
           qp.promotion_status AS promotion_status,
           qp.occurrence_count AS occurrence_count,
           qp.liked_count AS liked_count,
           coalesce(qp.cross_thread_likes, 0) AS cross_thread_likes,
           coalesce(qp.cross_thread_dislikes, 0) AS cross_thread_dislikes,
           CASE WHEN qp.last_seen IS NOT NULL
                THEN duration.between(qp.last_seen, datetime()).days
                ELSE null END AS last_seen_days,
           score AS raw_score,
           boosted_score AS score
    ORDER BY boosted_score DESC
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"embedding": embedding, "threshold": threshold})
        logger.debug("neo4j | fn=search_query_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_query_patterns failed (no patterns yet) | error={}", e)
        return []


@neo4j_breaker
def search_query_patterns_fts(question: str, limit: int = 5) -> list[dict]:
    """Fulltext search for QueryPatterns by question_text."""
    cypher = f"""
    CALL db.index.fulltext.queryNodes('querypattern_question_fts', $q)
    YIELD node AS qp, score
    WHERE qp.is_enabled = true
      AND coalesce(qp.promotion_status, 'active') <> 'demoted'
      AND coalesce(qp.occurrence_count, 0) >= 1
    RETURN qp.id AS id, qp.question_text AS question_text,
           qp.sql_text AS sql_text,
           qp.sql_cte_outline AS sql_cte_outline,
           qp.join_outline AS join_outline,
           qp.filter_summary AS filter_summary,
           qp.measure_summary AS measure_summary,
           qp.dimension_summary AS dimension_summary,
           qp.directive_summary AS directive_summary,
           qp.tables_used AS tables_used,
           qp.intent AS intent, qp.complexity AS complexity,
           qp.recompile_count AS recompile_count,
           qp.repair_count AS repair_count,
           qp.promotion_status AS promotion_status,
           qp.occurrence_count AS occurrence_count,
           qp.liked_count AS liked_count,
           coalesce(qp.cross_thread_likes, 0) AS cross_thread_likes,
           coalesce(qp.cross_thread_dislikes, 0) AS cross_thread_dislikes,
           null AS last_seen_days,
           score AS raw_score,
           score AS score
    ORDER BY score DESC LIMIT {limit}
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(cypher, {"q": _fuzzy_fts(question)})
        logger.debug("neo4j | fn=search_query_patterns_fts | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_query_patterns_fts failed | error={}", e)
        return []


def search_query_patterns_hybrid(
    embedding: list[float],
    question: str,
    threshold: float = 0.65,
    limit: int = 5,
) -> list[dict]:
    """Hybrid QueryPattern retrieval: vector cosine + FTS merged by best score per pattern id.

    Vector scores are on [0,1] (cosine). FTS scores are Lucene BM25 (unbounded above 1).
    Normalise FTS to [0,1] by capping at 5.0 before merging so neither signal dominates.
    """
    vec_results = search_query_patterns(embedding, threshold=threshold, limit=limit)
    fts_results = search_query_patterns_fts(question, limit=limit)

    _FTS_CAP = 5.0
    merged: dict[str, dict] = {}
    for r in vec_results:
        pid = r.get("id")
        if pid:
            merged[pid] = dict(r)

    for r in fts_results:
        pid = r.get("id")
        if not pid:
            continue
        fts_norm = min((r.get("raw_score") or 0.0), _FTS_CAP) / _FTS_CAP
        if pid in merged:
            if fts_norm > merged[pid].get("score", 0):
                merged[pid]["score"] = fts_norm
        else:
            entry = dict(r)
            entry["score"] = fts_norm
            merged[pid] = entry

    results = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)[:limit]
    logger.debug("neo4j | fn=search_query_patterns_hybrid | vec={} | fts={} | merged={}", len(vec_results), len(fts_results), len(results))
    return results


def find_canonical_pattern_id(
    embedding: list[float],
    intent: str,
    tables_used: list[str],
    threshold: float = 0.85,
) -> str | None:
    """Return id of an existing QueryPattern that is semantically equivalent to this execution.

    Dedup guards (all must pass):
    1. Embedding cosine similarity >= threshold (0.85)
    2. intent matches exactly
    3. >= 50% table overlap (skipped if either side is empty)
    4. Candidate node must not be 'demoted'

    Returns None if no match — caller should create a fresh UUID node.
    Called synchronously from pipeline.py via asyncio.to_thread().
    """
    candidates = search_query_patterns(embedding, threshold=threshold, limit=3)
    if not candidates:
        return None
    tables_set = set(tables_used or [])
    for r in candidates:
        if r.get("intent") != intent:
            continue
        if r.get("promotion_status") == "demoted":
            continue
        candidate_tables = set(r.get("tables_used") or [])
        if tables_set and candidate_tables:
            overlap = len(tables_set & candidate_tables) / max(len(tables_set), len(candidate_tables))
            if overlap < 0.5:
                continue
        return r["id"]
    return None



@neo4j_breaker
def search_query_patterns_by_tables(
    tables: list[str],
    limit: int = 5,
    min_overlap: float = 0.5,
) -> list[dict]:
    """Table-based QueryPattern lookup — Path B of hybrid retrieval.

    Finds patterns where >= min_overlap fraction of the stored pattern's tables
    are present in the current anchor_tables list.  Order-independent set
    intersection: works for subsets (current has 2, pattern has 3 → 0.67)
    and supersets (current has 4, pattern has 3 → 1.0).

    Runs in parallel with the vector search; results are merged by the caller.
    """
    if not tables:
        return []
    query = f"""
    MATCH (qp:QueryPattern)
    WHERE qp.is_enabled = true
      AND size(qp.tables_used) > 0
      AND ANY(t IN qp.tables_used WHERE t IN $tables)
      AND coalesce(qp.promotion_status, 'active') <> 'demoted'
    WITH qp,
         toFloat(size([t IN qp.tables_used WHERE t IN $tables]))
         / size(qp.tables_used) AS table_overlap
    WHERE table_overlap >= $min_overlap
    WITH qp, table_overlap,
         table_overlap
         * (1.0 + log(1.0 + coalesce(qp.occurrence_count, 1)) * 0.1)
         * CASE WHEN qp.last_seen IS NOT NULL
                  AND duration.between(qp.last_seen, datetime()).days < 30
                THEN 1.1 ELSE 1.0 END
         AS boosted_score
    RETURN qp.id AS id, qp.question_text AS question_text,
           qp.sql_text AS sql_text,
           qp.sql_cte_outline AS sql_cte_outline,
           qp.join_outline AS join_outline,
           qp.filter_summary AS filter_summary,
           qp.measure_summary AS measure_summary,
           qp.dimension_summary AS dimension_summary,
           qp.directive_summary AS directive_summary,
           qp.tables_used AS tables_used,
           qp.intent AS intent, qp.complexity AS complexity,
           qp.recompile_count AS recompile_count,
           qp.repair_count AS repair_count,
           qp.promotion_status AS promotion_status,
           qp.occurrence_count AS occurrence_count,
           qp.liked_count AS liked_count,
           coalesce(qp.cross_thread_likes, 0) AS cross_thread_likes,
           coalesce(qp.cross_thread_dislikes, 0) AS cross_thread_dislikes,
           CASE WHEN qp.last_seen IS NOT NULL
                THEN duration.between(qp.last_seen, datetime()).days
                ELSE null END AS last_seen_days,
           table_overlap AS raw_score,
           boosted_score AS score
    ORDER BY boosted_score DESC
    LIMIT {limit}
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"tables": tables, "min_overlap": min_overlap})
        logger.debug(
            "neo4j | fn=search_query_patterns_by_tables | ms={:.0f} | hits={}",
            (time.monotonic() - t0) * 1000, len(results),
        )
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_query_patterns_by_tables failed | error={}", e)
        return []


@neo4j_breaker
def search_anti_patterns_by_tables(
    tables: list[str],
    limit: int = 5,
    min_overlap: float = 0.5,
) -> list[dict]:
    """Table-based AntiPattern lookup — Path B of hybrid retrieval.

    tables_involved is a comma-separated string on AntiPattern nodes.
    Splits on comma, trims whitespace, then applies the same set-overlap logic
    as search_query_patterns_by_tables.
    """
    if not tables:
        return []
    query = f"""
    MATCH (ap:AntiPattern)
    WHERE ap.is_enabled = true
      AND ap.tables_involved IS NOT NULL AND ap.tables_involved <> ''
      AND (ap.success_count IS NULL OR ap.success_count < 3)
    WITH ap, [t IN split(ap.tables_involved, ',') | trim(t)] AS ap_tables
    WHERE ANY(t IN ap_tables WHERE t IN $tables)
    WITH ap, ap_tables,
         toFloat(size([t IN ap_tables WHERE t IN $tables]))
         / size(ap_tables) AS table_overlap
    WHERE table_overlap >= $min_overlap
    WITH ap, table_overlap,
         table_overlap
         * (1.0 + log(1.0 + coalesce(ap.occurrence_count, 1)) * 0.15)
         * CASE WHEN ap.last_seen IS NOT NULL
                  AND duration.between(ap.last_seen, datetime()).days < 30
                THEN 1.1 ELSE 1.0 END
         AS boosted_score
    RETURN ap.id AS id, ap.question_text AS query_text,
           ap.error_type AS error_type,
           coalesce(ap.error_detail, ap.error_summary, '') AS error_summary,
           ap.failing_element AS failing_element, ap.complexity AS complexity,
           ap.occurrence_count AS occurrence_count,
           ap.tables_involved AS tables_involved,
           boosted_score AS score
    ORDER BY boosted_score DESC
    LIMIT {limit}
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"tables": tables, "min_overlap": min_overlap})
        logger.debug(
            "neo4j | fn=search_anti_patterns_by_tables | ms={:.0f} | hits={}",
            (time.monotonic() - t0) * 1000, len(results),
        )
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_anti_patterns_by_tables failed | error={}", e)
        return []


@neo4j_breaker
def get_query_patterns_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    query = "MATCH (qp:QueryPattern) WHERE qp.id IN $ids RETURN properties(qp) AS qp"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_query_patterns_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["qp"] or {}) for r in results]


# ── AntiPattern ───────────────────────────────────────────────────────────────

@neo4j_breaker
def search_anti_patterns(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (ap:AntiPattern)
    SEARCH ap IN (VECTOR INDEX `antipattern_cohere_embedding` FOR $embedding LIMIT 5)
    SCORE AS score
    WHERE score > 0.65
      AND ap.is_enabled = true
      AND (ap.success_count IS NULL OR ap.success_count < 3)
    WITH ap, score,
         score
         * (1.0 + log(1.0 + coalesce(ap.occurrence_count, 1)) * 0.15)
         * CASE WHEN ap.last_seen IS NOT NULL
                  AND duration.between(ap.last_seen, datetime()).days < 30
                THEN 1.1 ELSE 1.0 END
         AS boosted_score
    RETURN ap.id AS id, ap.question_text AS query_text,
           ap.error_type AS error_type, coalesce(ap.error_detail, ap.error_summary, '') AS error_summary,
           ap.failing_element AS failing_element, ap.complexity AS complexity,
           ap.occurrence_count AS occurrence_count,
           ap.tables_involved AS tables_involved,
           boosted_score AS score
    ORDER BY boosted_score DESC
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"embedding": embedding})
        logger.debug("neo4j | fn=search_anti_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_anti_patterns failed (no patterns yet) | error={}", e)
        return []


@neo4j_breaker
def search_anti_patterns_fts(question: str, limit: int = 5) -> list[dict]:
    """Fulltext search for AntiPatterns by question_text."""
    cypher = f"""
    CALL db.index.fulltext.queryNodes('antipattern_question_fts', $q)
    YIELD node AS ap, score
    WHERE ap.is_enabled = true
      AND (ap.success_count IS NULL OR ap.success_count < 3)
    RETURN ap.id AS id, ap.question_text AS query_text,
           ap.error_type AS error_type,
           coalesce(ap.error_detail, ap.error_summary, '') AS error_summary,
           ap.failing_element AS failing_element, ap.complexity AS complexity,
           ap.occurrence_count AS occurrence_count,
           ap.tables_involved AS tables_involved,
           score AS score
    ORDER BY score DESC LIMIT {limit}
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(cypher, {"q": _fuzzy_fts(question)})
        logger.debug("neo4j | fn=search_anti_patterns_fts | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_anti_patterns_fts failed | error={}", e)
        return []


def search_anti_patterns_hybrid(
    embedding: list[float],
    question: str,
    limit: int = 5,
) -> list[dict]:
    """Hybrid AntiPattern retrieval: vector cosine + FTS merged by best score per id."""
    vec_results = search_anti_patterns(embedding)
    fts_results = search_anti_patterns_fts(question, limit=limit)

    _FTS_CAP = 5.0
    merged: dict[str, dict] = {}
    for r in vec_results:
        pid = r.get("id")
        if pid:
            merged[pid] = dict(r)

    for r in fts_results:
        pid = r.get("id")
        if not pid:
            continue
        fts_norm = min((r.get("score") or 0.0), _FTS_CAP) / _FTS_CAP
        if pid in merged:
            if fts_norm > merged[pid].get("score", 0):
                merged[pid]["score"] = fts_norm
        else:
            entry = dict(r)
            entry["score"] = fts_norm
            merged[pid] = entry

    results = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)[:limit]
    logger.debug("neo4j | fn=search_anti_patterns_hybrid | vec={} | fts={} | merged={}", len(vec_results), len(fts_results), len(results))
    return results


@neo4j_breaker
def get_anti_patterns_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    query = "MATCH (ap:AntiPattern) WHERE ap.id IN $ids RETURN properties(ap) AS ap"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": ids})
    logger.debug("neo4j | fn=get_anti_patterns_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["ap"] or {}) for r in results]


# ── Intent ────────────────────────────────────────────────────────────────────

@neo4j_breaker
def search_intents(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (i:Intent)
    SEARCH i IN (VECTOR INDEX `intent_cohere` FOR $embedding LIMIT 10)
    SCORE AS score
    RETURN i.name AS name, i.description AS description, score
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]



# ── BusinessTerm ──────────────────────────────────────────────────────────────

@neo4j_breaker
def search_business_terms_vector(embedding: list[float]) -> list[dict]:
    query = """CYPHER 25
    MATCH (bt:BusinessTerm)
    SEARCH bt IN (VECTOR INDEX `businessterm_cohere` FOR $embedding LIMIT 5)
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
def search_business_terms_fulltext(query_text: str) -> list[dict]:
    cypher = """
    CALL db.index.fulltext.queryNodes('businessterm_ft', $query)
    YIELD node AS bt, score
    RETURN bt.term AS term, bt.variants AS variants,
           bt.term_type AS term_type, bt.description AS description,
           score LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"query": _fuzzy_fts(query_text)})
    logger.debug("neo4j | fn=search_business_terms_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


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


@neo4j_breaker
def get_business_terms_by_terms(terms: list[str]) -> list[dict]:
    if not terms:
        return []
    query = "MATCH (bt:BusinessTerm) WHERE bt.term IN $terms RETURN properties(bt) AS bt"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"terms": terms})
    logger.debug("neo4j | fn=get_business_terms_by_terms | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["bt"] or {}) for r in results]


@neo4j_breaker
def get_business_terms_for_tables(anchor_fqns: list[str]) -> list[dict]:
    """Return BusinessTerms linked to anchor tables via REFERENCES_TABLE edges.

    Returns each BT's term, description, term_category, term_type, and variants.
    Used by schema_enricher to build concept_mappings shown to directive_writer.
    BusinessTerm nodes do NOT have definition/computation/sql_expression properties.
    """
    if not anchor_fqns:
        return []
    query = """
    MATCH (bt:BusinessTerm)-[:REFERENCES_TABLE]->(t:Table)
    WHERE t.fqn IN $anchor_fqns
    RETURN bt.term AS term, bt.description AS description,
           bt.term_category AS term_category,
           bt.term_type AS term_type, bt.variants AS variants,
           t.fqn AS table_fqn
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"anchor_fqns": anchor_fqns})
    logger.debug("neo4j | fn=get_business_terms_for_tables | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def get_all_domain_names() -> list[str]:
    """Return all Domain node names ordered by table count (most coverage first).

    Used by intake_classifier to build the system scope list — what business
    areas the assistant can answer questions about.
    """
    query = """
    MATCH (d:Domain)
    RETURN d.name AS name, coalesce(d.table_count, 0) AS table_count
    ORDER BY table_count DESC
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {})
    logger.debug("neo4j | fn=get_all_domain_names | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [r["name"] for r in results if r.get("name")]


@neo4j_breaker
def get_all_intent_names() -> list[str]:
    """Return all Intent node names + brief descriptions.

    Used by intake_classifier to enumerate the analytical patterns the
    system can answer — e.g. 'balance_lookup', 'trend_analysis', etc.
    """
    query = """
    MATCH (i:Intent)
    RETURN i.name AS name, i.description AS description
    ORDER BY i.name
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {})
    logger.debug("neo4j | fn=get_all_intent_names | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [
        f"{r['name']}: {(r.get('description') or '')[:80].rstrip()}"
        for r in results
        if r.get("name")
    ]


@neo4j_breaker
def get_tables_for_canonical_domains(domain_names: list[str]) -> list[dict]:
    """Direct Domain→Table lookup using canonical domain names from query_intent DOMAIN lines.

    Returns [{fqn, description, domain_name}] for tables belonging to any of the named domains.
    Used by context_fetcher to guarantee domain coverage for multi-domain queries (Q3 pattern).
    Falls back to empty list on any failure — never blocks the pipeline.
    """
    if not domain_names:
        return []
    query = """
    MATCH (t:Table)-[:BELONGS_TO]->(d:Domain)
    WHERE d.name IN $names
    RETURN t.fqn AS fqn,
           coalesce(t.description, '') AS description,
           d.name AS domain_name
    ORDER BY t.fqn
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"names": domain_names})
    logger.debug(
        "neo4j | fn=get_tables_for_canonical_domains | domains={} | ms={:.0f} | hits={}",
        domain_names, (time.monotonic() - t0) * 1000, len(results),
    )
    return [dict(r) for r in results if r.get("fqn")]

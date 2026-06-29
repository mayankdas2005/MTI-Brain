"""Table search functions — all paths that return Table node data."""

from __future__ import annotations

import re as _re
import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_run

_LUCENE_SAFE = _re.compile(r'[^\w\s]', _re.UNICODE)
# Lucene fuzzy clauses = sum of vocabulary terms within edit-distance-1 of each
# query token.  Empirically ~100 expansions/token against this index vocabulary.
# Neo4j default maxClauseCount = 1024.  Cap at 8 tokens → ≤800 clauses.
_MAX_FTS_TOKENS = 8


def _fuzzy_fts(text: str, min_len: int = 3) -> str:
    """Append Lucene edit-distance-1 (~) to each token >= min_len chars.

    Strips any non-word, non-whitespace Unicode character (em-dashes, curly
    quotes, math operators, etc.) so user input of any form doesn't produce a
    ParseException in the Neo4j FTS index.

    Token count is capped at _MAX_FTS_TOKENS to prevent TooManyClauses errors
    when long natural-language questions are passed as the FTS query.
    """
    tokens = []
    for raw in text.split():
        t = _LUCENE_SAFE.sub("", raw)
        if not t:
            continue
        tokens.append(t + "~" if len(t) >= min_len else t)
        if len(tokens) >= _MAX_FTS_TOKENS:
            break
    return " ".join(tokens) if tokens else text

# Shared RETURN clause for table properties — used by all table search functions.
# No numeric scores exposed — use matched_via for path attribution.
_TABLE_RETURN = """
    t.fqn AS fqn, t.name AS name, t.description AS description,
    t.grain AS grain, t.synonyms AS synonyms,
    t.business_domain AS business_domain, t.community_id AS community_id,
    t.typical_join_role AS typical_join_role, t.table_type AS table_type,
    t.natural_dimensions AS natural_dimensions,
    t.natural_measures AS natural_measures,
    t.is_dimension_hub AS is_dimension_hub,
    t.hub_join_col AS hub_join_col,
    t.has_seasonality_pattern AS has_seasonality_pattern,
    t.typical_lookback_days AS typical_lookback_days,
    t.betweenness_score AS betweenness_score,
    t.in_degree AS in_degree,
    t.pk_columns AS pk_columns,
    t.pagerank_score AS pagerank_score,
    t.intent_tags AS intent_tags
"""


@neo4j_breaker
def search_tables_vector(embedding: list[float]) -> list[dict]:
    query = f"""CYPHER 25
    MATCH (t:Table)
    SEARCH t IN (VECTOR INDEX `tbl_cohere_embedding` FOR $embedding LIMIT 10)
    SCORE AS score
    RETURN {_TABLE_RETURN}, score, 'direct_vector' AS matched_via
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_tables_vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_fulltext(query_text: str) -> list[dict]:
    cypher = f"""
    CALL db.index.fulltext.queryNodes('table_ft_extended', $query)
    YIELD node AS t, score
    RETURN {_TABLE_RETURN}, score, 'direct_fts' AS matched_via LIMIT 10
    """
    try:
        t0 = time.monotonic()
        results = _neo4j_run(cypher, {"query": _fuzzy_fts(query_text)})
        logger.debug("neo4j | fn=search_tables_fulltext | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_tables_fulltext fts failed | error={}", e)
        return []


@neo4j_breaker
def search_tables_via_intents(embedding: list[float]) -> list[dict]:
    query = f"""CYPHER 25
    MATCH (i:Intent)
    SEARCH i IN (VECTOR INDEX `intent_cohere` FOR $embedding LIMIT 10)
    SCORE AS intent_score
    WITH i, intent_score
    MATCH (t:Table)-[:RELEVANT_TO]->(i)
    RETURN {_TABLE_RETURN}, intent_score AS score, i.name AS matched_via
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_tables_via_intents | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_community(embedding: list[float]) -> list[dict]:
    query = f"""CYPHER 25
    MATCH (c:Community)
    SEARCH c IN (VECTOR INDEX `community_cohere` FOR $embedding LIMIT 3)
    SCORE AS community_score
    WITH c, community_score
    MATCH (c)-[:CONTAINS_TABLE]->(t:Table)
    RETURN {_TABLE_RETURN}, community_score AS score, c.dominant_domain AS matched_via
    ORDER BY community_score DESC LIMIT 15
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_tables_via_community | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_domain(embedding: list[float]) -> list[dict]:
    query = f"""CYPHER 25
    MATCH (d:Domain)
    SEARCH d IN (VECTOR INDEX `domain_cohere` FOR $embedding LIMIT 3)
    SCORE AS domain_score
    WITH d, domain_score
    MATCH (t:Table)-[:BELONGS_TO]->(d)
    RETURN {_TABLE_RETURN}, domain_score AS score, d.name AS matched_via
    ORDER BY domain_score DESC LIMIT 20
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"embedding": embedding})
    logger.debug("neo4j | fn=search_tables_via_domain | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_joinpaths(candidate_fqns: list[str]) -> list[dict]:
    if not candidate_fqns:
        return []
    query = f"""
    MATCH (jp:JoinPath)
    WHERE (jp.from_fqn IN $fqns OR jp.to_fqn IN $fqns)
      AND jp.quality_score >= 0.3
      AND jp.hop_count <= 3
    WITH jp,
         CASE WHEN jp.from_fqn IN $fqns THEN jp.to_fqn ELSE jp.from_fqn END AS target_fqn,
         CASE jp.algorithm WHEN 'dijkstra' THEN 0 ELSE jp.k_rank END AS path_priority
    WHERE NOT target_fqn IN $fqns
    ORDER BY path_priority ASC, jp.quality_score DESC
    WITH target_fqn, COLLECT(jp)[0] AS best_jp
    MATCH (t:Table {{fqn: target_fqn}})
    RETURN {_TABLE_RETURN},
           best_jp.quality_score AS score,
           (best_jp.from_fqn + ' -> ' + best_jp.to_fqn) AS matched_via,
           best_jp.join_clauses  AS join_clauses,
           best_jp.path_tables   AS path_tables
    ORDER BY best_jp.quality_score DESC LIMIT 5
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": candidate_fqns})
    logger.debug("neo4j | fn=search_tables_via_joinpaths | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_filter_values(tokens: list[str]) -> list[dict]:
    """Path 9: find tables where question tokens match column value_aliases or distinct_values.

    Primary: value_aliases items e.g. "BANK_JPM -> JPMorgan Chase" — catches human entity names
    Fallback: distinct_values items when value_aliases is empty — catches code/enum values
    Secondary: column description text — catches entity names mentioned in schema docs
    Tokens < 4 chars excluded to avoid common word noise.
    """
    if not tokens:
        return []
    significant = list({t for t in tokens if len(t) >= 4})[:25]
    if not significant:
        return []
    query = f"""
    UNWIND $tokens AS token
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE (
      (c.value_aliases IS NOT NULL AND SIZE(c.value_aliases) > 0
        AND ANY(a IN c.value_aliases WHERE toLower(a) CONTAINS token))
      OR
      (
        (c.value_aliases IS NULL OR SIZE(c.value_aliases) = 0)
        AND c.distinct_values IS NOT NULL
        AND ANY(v IN c.distinct_values WHERE toLower(v) CONTAINS token)
      )
      OR
      (c.description IS NOT NULL AND toLower(c.description) CONTAINS token)
    )
    WITH t, c, token,
         [a IN coalesce(c.value_aliases, [])   WHERE toLower(a) CONTAINS token] AS alias_hits,
         [v IN coalesce(c.distinct_values, []) WHERE toLower(v) CONTAINS token] AS dv_hits,
         coalesce(c.description, '')                                             AS col_desc,
         size(coalesce(c.value_aliases, []))                                     AS va_size
    WITH t,
         c.name             AS matched_col,
         token,
         t.pagerank_score   AS pagerank,
         CASE
           WHEN size(alias_hits) > 0                  THEN alias_hits[0]
           WHEN va_size = 0 AND size(dv_hits) > 0     THEN dv_hits[0]
           ELSE substring(col_desc, 0, 80)
         END AS matched_item,
         CASE
           WHEN size(alias_hits) > 0                  THEN 3
           WHEN va_size = 0 AND size(dv_hits) > 0     THEN 2
           ELSE 1
         END AS match_score
    WHERE matched_item IS NOT NULL
    ORDER BY match_score DESC, pagerank DESC
    WITH t, COLLECT({{col: matched_col, item: matched_item, token: token}})[0] AS best_match,
         MAX(pagerank) AS top_score,
         MAX(match_score) AS top_match_score
    RETURN {_TABLE_RETURN},
           top_score AS score,
           best_match.col   AS entity_matched_column,
           best_match.item  AS entity_matched_value,
           best_match.token AS entity_matched_token,
           top_match_score  AS entity_match_score,
           ('entity:' + best_match.col + '~=' + best_match.token) AS matched_via
    ORDER BY top_match_score DESC, top_score DESC
    LIMIT 8
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"tokens": significant})
    logger.debug(
        "neo4j | fn=search_tables_via_filter_values | ms={:.0f} | hits={}",
        (time.monotonic() - t0) * 1000, len(results),
    )
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_business_terms(embedding: list[float], query_text: str) -> list[dict]:
    """Path D: BusinessTerm hybrid → REFERENCES_TABLE → Table.

    Note: source_column_name on REFERENCES_TABLE edge may be inaccurate.
    Use related_table_fqns on BusinessTerm node — it is reliable.
    """
    results: list[dict] = []
    seen: set[str] = set()

    # Vector pass
    vector_query = f"""CYPHER 25
    MATCH (bt:BusinessTerm)
    SEARCH bt IN (VECTOR INDEX `businessterm_cohere` FOR $embedding LIMIT 8)
    SCORE AS score
    WHERE score > 0.60
    MATCH (bt)-[:REFERENCES_TABLE]->(t:Table)
    RETURN {_TABLE_RETURN}, score, 'businessterm_v' AS matched_via LIMIT 12
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(vector_query, {"embedding": embedding})
        logger.debug("neo4j | fn=search_tables_via_business_terms | vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            d = dict(r)
            if d.get("fqn") and d["fqn"] not in seen:
                seen.add(d["fqn"])
                results.append(d)
    except Exception as e:
        logger.warning("neo4j | search_tables_via_business_terms vector failed | error={}", e)

    # Fulltext pass
    fts_query = f"""
    CALL db.index.fulltext.queryNodes('businessterm_ft', $query)
    YIELD node AS bt, score
    WHERE score > 0.5
    MATCH (bt)-[:REFERENCES_TABLE]->(t:Table)
    RETURN {_TABLE_RETURN}, score, 'businessterm_ft' AS matched_via LIMIT 10
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(fts_query, {"query": _fuzzy_fts(query_text)})
        logger.debug("neo4j | fn=search_tables_via_business_terms | fts | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            d = dict(r)
            if d.get("fqn") and d["fqn"] not in seen:
                seen.add(d["fqn"])
                results.append(d)
    except Exception as e:
        logger.warning("neo4j | search_tables_via_business_terms fts failed | error={}", e)

    return results[:10]


@neo4j_breaker
def search_tables_via_columns(embedding: list[float], query_text: str) -> list[dict]:
    """Path E: Column hybrid search → deduplicate by table_fqn → Table nodes."""
    results: list[dict] = []
    seen: set[str] = set()

    vector_query = f"""CYPHER 25
    MATCH (c:Column)
    SEARCH c IN (VECTOR INDEX `col_cohere_embedding` FOR $embedding LIMIT 20)
    SCORE AS score
    WITH DISTINCT c.table_fqn AS fqn, max(score) AS top_score
    MATCH (t:Table {{fqn: fqn}})
    RETURN {_TABLE_RETURN}, top_score AS score, 'column_v' AS matched_via LIMIT 10
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(vector_query, {"embedding": embedding})
        logger.debug("neo4j | fn=search_tables_via_columns | vector | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            d = dict(r)
            if d.get("fqn") and d["fqn"] not in seen:
                seen.add(d["fqn"])
                results.append(d)
    except Exception as e:
        logger.warning("neo4j | search_tables_via_columns vector failed | error={}", e)

    fts_query = f"""
    CALL db.index.fulltext.queryNodes('col_ft_extended', $query)
    YIELD node AS c, score
    WITH DISTINCT c.table_fqn AS fqn, max(score) AS top_score
    MATCH (t:Table {{fqn: fqn}})
    RETURN {_TABLE_RETURN}, top_score AS score, 'column_ft' AS matched_via LIMIT 10
    """
    try:
        t0 = time.monotonic()
        rows = _neo4j_run(fts_query, {"query": _fuzzy_fts(query_text)})
        logger.debug("neo4j | fn=search_tables_via_columns | fts | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(rows))
        for r in rows:
            d = dict(r)
            if d.get("fqn") and d["fqn"] not in seen:
                seen.add(d["fqn"])
                results.append(d)
    except Exception as e:
        logger.warning("neo4j | search_tables_via_columns fts failed | error={}", e)

    return results[:10]


@neo4j_breaker
def search_tables_from_query_patterns(embedding: list[float]) -> list[dict]:
    """Path H: QueryPattern → tables_used — ground truth from successful prior queries.

    Only fires when score > 0.85 (confirmed similar question was answered before).
    Returns tables from the matched pattern's tables_used list.
    """
    query = f"""CYPHER 25
    MATCH (qp:QueryPattern)
    SEARCH qp IN (VECTOR INDEX `querypattern_cohere_embedding` FOR $embedding LIMIT 2)
    SCORE AS score
    WHERE score > 0.85
      AND qp.is_enabled = true
    UNWIND qp.tables_used AS table_fqn
    MATCH (t:Table {{fqn: table_fqn}})
    RETURN {_TABLE_RETURN}, score, qp.id AS matched_via LIMIT 15
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"embedding": embedding})
        logger.debug("neo4j | fn=search_tables_from_query_patterns | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_tables_from_query_patterns failed (no patterns yet) | error={}", e)
        return []


@neo4j_breaker
def get_tables_with_context(table_fqns: list[str]) -> list[dict]:
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
def get_community_bridges(community_ids: list) -> list[dict]:
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
def get_business_term_table_edges(bt_terms: list[str], anchor_fqns: list[str]) -> list[dict]:
    """Fetch real REFERENCES_TABLE edges from BusinessTerms to anchor tables.

    Used by graph_context_builder to show which BusinessTerms actually pointed to
    the tables used in the query — replaces the old fake CONTEXT_RELEVANT edges.
    Only returns edges where BOTH the term AND the table were used in this query.
    """
    if not bt_terms or not anchor_fqns:
        return []
    query = """
    MATCH (bt:BusinessTerm)-[r:REFERENCES_TABLE]->(t:Table)
    WHERE bt.term IN $bt_terms AND t.fqn IN $anchor_fqns
    RETURN bt.term AS term, t.fqn AS table_fqn,
           r.source_column_name AS source_column_name
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"bt_terms": bt_terms, "anchor_fqns": anchor_fqns})
    logger.debug("neo4j | fn=get_business_term_table_edges | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def search_tables_via_query_templates(embedding: list[float]) -> list[dict]:
    """Path T: QueryTemplate → REQUIRES_TABLE → Table.

    QueryTemplates encode known query structures for specific business questions.
    Their REQUIRES_TABLE edges pin anchor tables that are structurally required.
    Only fires on high-similarity matches (score > 0.75).
    """
    query = f"""CYPHER 25
    MATCH (qt:QueryTemplate)
    SEARCH qt IN (VECTOR INDEX `querytemplate_cohere` FOR $embedding LIMIT 5)
    SCORE AS score
    WHERE score > 0.70
    MATCH (qt)-[:REQUIRES_TABLE]->(t:Table)
    RETURN {_TABLE_RETURN}, score, qt.id AS matched_via
    ORDER BY score DESC LIMIT 15
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"embedding": embedding})
        logger.debug("neo4j | fn=search_tables_via_query_templates | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_tables_via_query_templates failed | error={}", e)
        return []


@neo4j_breaker
def get_business_terms_with_related_tables(embedding: list[float], query_text: str) -> list[dict]:
    """Return high-confidence BusinessTerm matches with their related_table_fqns.

    Used for entity pinning in table_discovery — related_table_fqns on high-score
    BusinessTerms are pinned and survive the MAX_ANCHOR_TABLES cap.
    Returns: [{term, score, related_table_fqns: list[str], variants: list[str]}]
    """
    results: list[dict] = []
    seen: set[str] = set()

    vector_query = """CYPHER 25
    MATCH (bt:BusinessTerm)
    SEARCH bt IN (VECTOR INDEX `businessterm_cohere` FOR $embedding LIMIT 5)
    SCORE AS score
    WHERE score > 0.72 AND bt.related_table_fqns IS NOT NULL
    RETURN bt.term AS term, bt.variants AS variants,
           bt.related_table_fqns AS related_table_fqns, score
    """
    try:
        rows = _neo4j_run(vector_query, {"embedding": embedding})
        for r in rows:
            term = r.get("term")
            if term and term not in seen:
                seen.add(term)
                results.append(dict(r))
    except Exception as e:
        logger.warning("neo4j | get_business_terms_with_related_tables vector failed | error={}", e)

    fts_query = """
    CALL db.index.fulltext.queryNodes('businessterm_ft', $query)
    YIELD node AS bt, score
    WHERE score > 0.6 AND bt.related_table_fqns IS NOT NULL
    RETURN bt.term AS term, bt.variants AS variants,
           bt.related_table_fqns AS related_table_fqns, score
    LIMIT 5
    """
    try:
        rows = _neo4j_run(fts_query, {"query": _fuzzy_fts(query_text)})
        for r in rows:
            term = r.get("term")
            if term and term not in seen:
                seen.add(term)
                results.append(dict(r))
    except Exception as e:
        logger.warning("neo4j | get_business_terms_with_related_tables fts failed | error={}", e)

    logger.debug("neo4j | fn=get_business_terms_with_related_tables | hits={}", len(results))
    return results

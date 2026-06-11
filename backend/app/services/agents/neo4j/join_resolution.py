"""Join path resolution — 7-tier cascade from JOINS_TO through JoinPath nodes."""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_run, _neo4j_run_single


@neo4j_breaker
def get_direct_joins(table_fqns: list[str]) -> list[dict]:
    """Batch query JOINS_TO edges where both endpoints are in table_fqns.

    Returns from_fqn, to_fqn, from_col, to_col, join_type, confidence.
    Uses declared manually-curated joins from lpp_semantic_model.yml.
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
def load_join_path(from_fqn: str, to_fqn: str) -> dict | None:
    """Dijkstra shortest path — always k_rank=1 (there is only one Dijkstra path per pair)."""
    query = """
    MATCH (jp:JoinPath)
    WHERE (jp.from_fqn = $from AND jp.to_fqn = $to) OR (jp.from_fqn = $to AND jp.to_fqn = $from)
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
def load_join_path_yens(from_fqn: str, to_fqn: str, k_rank: int = 2) -> dict | None:
    """Yen's k-th alternative path. k_rank=2 is 2nd shortest, k_rank=3 is 3rd shortest.

    Dijkstra always uses k_rank=1. Yen's paths start at k_rank=2.
    """
    query = """
    MATCH (jp:JoinPath)
    WHERE (jp.from_fqn = $from AND jp.to_fqn = $to) OR (jp.from_fqn = $to AND jp.to_fqn = $from)
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



def load_best_join_path(from_fqn: str, to_fqn: str) -> dict | None:
    """Best available join path using 7-tier cascade. Returns None only when all tiers fail.

    Tiers:
      1. JOINS_TO direct FK edge (authoritative — both directions)
      2. JoinPath Dijkstra k_rank=1 forward
      3. JoinPath Dijkstra k_rank=1 reverse
      4. JoinPath Yen's k_rank=2 (forward + reverse)
      5. JoinPath Yen's k_rank=3 (forward + reverse)
    """
    # Tier 1: JOINS_TO direct FK
    try:
        edges = get_direct_joins([from_fqn, to_fqn])
        for e in edges:
            f, t = e.get("from_fqn"), e.get("to_fqn")
            fc, tc = e.get("from_col"), e.get("to_col")
            if not (f and t and fc and tc):
                continue
            if (f == from_fqn and t == to_fqn) or (f == to_fqn and t == from_fqn):
                clause = f"{f}.{fc} = {t}.{tc}"
                logger.info("neo4j | join via JOINS_TO (tier1) | from={} to={} | clause={}", from_fqn, to_fqn, clause)
                return {"id": "", "join_clauses": [clause], "path_tables": [f, t],
                        "hop_count": 1, "total_cost": None, "quality_score": None,
                        "is_cross_community": False, "tier": "joins_to"}
    except Exception as exc:
        logger.warning("neo4j | JOINS_TO edge lookup failed | from={} to={} | error={}", from_fqn, to_fqn, exc)

    # Tier 2+3: Dijkstra k_rank=1 forward then reverse
    result = load_join_path(from_fqn, to_fqn)
    if result:
        logger.info("neo4j | join via Dijkstra (tier2) | from={} to={}", from_fqn, to_fqn)
        result["tier"] = "dijkstra"
        return result

    result = load_join_path(to_fqn, from_fqn)
    if result:
        logger.info("neo4j | join via Dijkstra reversed (tier3) | from={} to={}", from_fqn, to_fqn)
        result["tier"] = "dijkstra_reversed"
        return result

    # Tier 4+5: Yen's k_rank=2,3
    for k in (2, 3):
        result = load_join_path_yens(from_fqn, to_fqn, k_rank=k)
        if result:
            logger.info("neo4j | join via Yen's k={} (tier {}) | from={} to={}", k, k + 2, from_fqn, to_fqn)
            result["tier"] = f"yens_k{k}"
            return result
        result = load_join_path_yens(to_fqn, from_fqn, k_rank=k)
        if result:
            logger.info("neo4j | join via Yen's k={} reversed | from={} to={}", k, from_fqn, to_fqn)
            result["tier"] = f"yens_k{k}_reversed"
            return result

    logger.warning("neo4j | NO join found (tried all tiers) | from={} to={}", from_fqn, to_fqn)
    return None



@neo4j_breaker
def get_join_paths_by_ids(path_ids: list[str]) -> list[dict]:
    if not path_ids:
        return []
    query = "MATCH (jp:JoinPath) WHERE jp.id IN $ids RETURN properties(jp) AS jp"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": path_ids})
    logger.debug("neo4j | fn=get_join_paths_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["jp"] or {}) for r in results]


@neo4j_breaker
def get_joinpath_joins(candidate_fqns: list[str]) -> list[dict]:
    """Return multi-hop JoinPath paths where BOTH endpoints are in candidate_fqns.

    Complements get_direct_joins — only called for pairs not covered by JOINS_TO edges.
    Prefers Dijkstra (k_rank=1) over Yen's alternatives per endpoint pair.
    quality_score >= 0.3 matches the discovery threshold in search_tables_via_joinpaths.
    """
    if len(candidate_fqns) < 2:
        return []
    query = """
    MATCH (jp:JoinPath)
    WHERE jp.from_fqn IN $fqns
      AND jp.to_fqn IN $fqns
      AND jp.hop_count >= 2
      AND jp.quality_score >= 0.3
    WITH jp.from_fqn AS from_fqn, jp.to_fqn AS to_fqn, COLLECT(jp) AS paths
    WITH from_fqn, to_fqn,
         COALESCE(
           [p IN paths WHERE p.algorithm = 'dijkstra'][0],
           paths[0]
         ) AS best_path
    WHERE best_path IS NOT NULL
    RETURN from_fqn, to_fqn,
           best_path.join_clauses  AS join_clauses,
           best_path.path_tables   AS path_tables,
           best_path.hop_count     AS hop_count,
           best_path.quality_score AS quality_score
    ORDER BY best_path.quality_score DESC
    LIMIT 10
    """
    t0 = time.monotonic()
    results = _neo4j_run(query, {"fqns": candidate_fqns})
    logger.debug(
        "neo4j | fn=get_joinpath_joins | ms={:.0f} | hits={}",
        (time.monotonic() - t0) * 1000, len(results),
    )
    return [dict(r) for r in results]



@neo4j_breaker
def find_join_via_graph_traversal(from_fqn: str, to_fqn: str) -> dict | None:
    """Last-resort fallback: shortest undirected JOINS_TO* path through intermediate tables.

    Used when no direct JOINS_TO edge or pre-computed JoinPath covers this pair.
    Traverses up to 4 hops in either direction. Returns join_clauses + path_tables
    (including any intermediate bridge tables), or None if no path exists.
    """
    query = """
    MATCH path = shortestPath((t1:Table)-[:JOINS_TO*1..4]-(t2:Table))
    WHERE t1.fqn = $from AND t2.fqn = $to
    WITH nodes(path) AS ns, relationships(path) AS rs
    RETURN
      [n IN ns | n.fqn] AS path_tables,
      [i IN range(0, size(rs)-1) |
        startNode(rs[i]).fqn + '.' + rs[i].from_col + ' = ' +
        endNode(rs[i]).fqn   + '.' + rs[i].to_col
      ] AS join_clauses,
      size(rs) AS hop_count
    LIMIT 1
    """
    t0 = time.monotonic()
    result = _neo4j_run_single(query, {"from": from_fqn, "to": to_fqn})
    logger.debug(
        "neo4j | fn=find_join_via_graph_traversal | from={} to={} | ms={:.0f} | found={}",
        from_fqn, to_fqn, (time.monotonic() - t0) * 1000, result is not None,
    )
    if not result:
        return None
    return {
        "from_fqn":     from_fqn,
        "to_fqn":       to_fqn,
        "join_clauses": list(result.get("join_clauses") or []),
        "path_tables":  list(result.get("path_tables") or []),
        "hop_count":    result.get("hop_count", 1),
        "source":       "graph_traversal",
    }


def get_all_join_paths_for_tables(fqns: list[str]) -> list[dict]:
    """All confirmed join paths between tables in fqns — direct JOINS_TO + pre-computed JoinPaths.

    Thin wrapper combining get_direct_joins() and get_joinpath_joins().
    Deduplicates by (from_fqn, to_fqn) pair — JOINS_TO (manually curated) takes precedence
    over JoinPath (algorithmic) when both cover the same pair.
    """
    if not fqns:
        return []
    direct = get_direct_joins(fqns)
    paths = get_joinpath_joins(fqns)
    seen: dict[tuple, dict] = {}
    for r in direct + paths:
        key = tuple(sorted([r.get("from_fqn", ""), r.get("to_fqn", "")]))
        if key not in seen or (r.get("quality_score") or 0) > (seen[key].get("quality_score") or 0):
            seen[key] = r
    return list(seen.values())

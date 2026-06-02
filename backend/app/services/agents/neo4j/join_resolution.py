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
def load_join_path_yens(from_fqn: str, to_fqn: str, k_rank: int = 2) -> dict | None:
    """Yen's k-th alternative path. k_rank=2 is 2nd shortest, k_rank=3 is 3rd shortest.

    Dijkstra always uses k_rank=1. Yen's paths start at k_rank=2.
    """
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
    """Explicit Dijkstra fetch. k_rank is always 1 for Dijkstra."""
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
    logger.debug("neo4j | fn=load_join_path_dijkstra | from={} to={} | ms={:.0f} | found={}", from_fqn, to_fqn, (time.monotonic() - t0) * 1000, result is not None)
    return dict(result) if result else None


def collect_all_join_paths(from_fqn: str, to_fqn: str) -> list[dict]:
    """Collect ALL available join paths — no early exit.

    Tier order:
      1. JOINS_TO direct FK edges (both directions)
      2. Dijkstra k_rank=1 forward + reverse
      3. Yen's k_rank=2,3 forward + reverse
    Deduplicates by normalized join_clauses content.
    """
    paths: list[dict] = []
    seen_clauses: set[str] = set()

    def _key(p: dict) -> str:
        return "|".join(sorted(str(c) for c in (p.get("join_clauses") or [])))

    def _add(path: dict | None, tier: str, direction: str = "forward") -> None:
        if not path:
            return
        key = _key(path)
        if key and key not in seen_clauses:
            seen_clauses.add(key)
            paths.append({**path, "tier": tier, "direction": direction})

    # Tier 1: JOINS_TO direct FK edges
    try:
        edges = get_direct_joins([from_fqn, to_fqn])
        for e in edges:
            f, t = e.get("from_fqn"), e.get("to_fqn")
            fc, tc = e.get("from_col"), e.get("to_col")
            if not (f and t and fc and tc):
                continue
            direction = "forward" if f == from_fqn else "reverse"
            clause = f"{f}.{fc} = {t}.{tc}"
            if clause not in seen_clauses:
                seen_clauses.add(clause)
                paths.append({
                    "id": "", "join_clauses": [clause],
                    "path_tables": [f, t], "hop_count": 1,
                    "total_cost": None, "quality_score": None,
                    "is_cross_community": False,
                    "tier": "joins_to", "direction": direction,
                })
    except Exception as exc:
        logger.warning("neo4j | collect_all_join_paths | JOINS_TO failed | {}", exc)

    # Tier 2: Dijkstra k_rank=1 (forward + reverse)
    _add(load_join_path_dijkstra(from_fqn, to_fqn, k_rank=1), tier="dijkstra_k1", direction="forward")
    _add(load_join_path_dijkstra(to_fqn, from_fqn, k_rank=1), tier="dijkstra_k1", direction="reverse")

    # Tier 3: Yen's k_rank=2,3 (forward + reverse)
    for k in (2, 3):
        _add(load_join_path_yens(from_fqn, to_fqn, k_rank=k), tier=f"yens_k{k}", direction="forward")
        _add(load_join_path_yens(to_fqn, from_fqn, k_rank=k), tier=f"yens_k{k}", direction="reverse")

    logger.info("neo4j | collect_all_join_paths | from={} to={} | total_paths={}", from_fqn, to_fqn, len(paths))
    return paths


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
def search_join_path_by_semantics(from_fqn: str, to_fqn: str) -> list[dict]:
    """Join Tier 7: find semantically similar column pairs between two tables.

    Used as a last resort when no JOINS_TO or JoinPath exists.
    Returns candidate join pairs with similarity score.
    """
    query = """
    MATCH (c1:Column {table_fqn: $from})-[r:SEMANTICALLY_SIMILAR]->(c2:Column {table_fqn: $to})
    WHERE r.similarity >= 0.88
    RETURN c1.name AS from_col, c2.name AS to_col, r.similarity AS confidence
    ORDER BY r.similarity DESC LIMIT 3
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"from": from_fqn, "to": to_fqn})
        logger.debug("neo4j | fn=search_join_path_by_semantics | from={} to={} | ms={:.0f} | hits={}", from_fqn, to_fqn, (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | search_join_path_by_semantics failed | error={}", e)
        return []


@neo4j_breaker
def get_join_paths_by_ids(path_ids: list[str]) -> list[dict]:
    if not path_ids:
        return []
    query = "MATCH (jp:JoinPath) WHERE jp.id IN $ids RETURN properties(jp) AS jp"
    t0 = time.monotonic()
    results = _neo4j_run(query, {"ids": path_ids})
    logger.debug("neo4j | fn=get_join_paths_by_ids | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r["jp"] or {}) for r in results]

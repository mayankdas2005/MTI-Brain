"""Cross-domain hub detection via BRIDGES_TO edges and dimension hub tables."""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_run


@neo4j_breaker
def get_dimension_hub_for_communities(community_ids: list) -> list[dict]:
    """Fetch BRIDGES_TO edges with hub info for the given community IDs.

    CRITICAL: Use hub_table_fqn (the conformed dimension used for cross-domain joins).
    This is DIFFERENT from bridge_table_fqn (the table that bridges two communities).
    hub_table_fqn is set by _pass_bridges_to_enhanced() in the ingestion pipeline.
    """
    if not community_ids:
        return []
    cypher = """
    MATCH (c1:Community)-[r:BRIDGES_TO]->(c2:Community)
    WHERE c1.id IN $ids AND c2.id IN $ids
      AND r.hub_table_fqn IS NOT NULL
    RETURN c1.id AS from_community, c2.id AS to_community,
           r.hub_table_fqn AS hub_table_fqn,
           r.hub_join_col AS hub_join_col,
           r.bridge_type AS bridge_type,
           r.join_safe AS join_safe,
           r.shared_dimension_columns AS shared_dimension_columns
    ORDER BY r.hub_join_col DESC
    """
    t0 = time.monotonic()
    results = _neo4j_run(cypher, {"ids": community_ids})
    logger.debug("neo4j | fn=get_dimension_hub_for_communities | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
    return [dict(r) for r in results]


@neo4j_breaker
def find_common_dimension_hub(fqns: list[str]) -> list[dict]:
    """Find dimension hub tables reachable from multiple anchor tables.

    Used as Method 4 fallback when BRIDGES_TO doesn't have hub_table_fqn set
    (i.e., _pass_bridges_to_enhanced() hasn't run yet).

    Returns hubs ordered by how many anchor tables reach them, then by in_degree.
    """
    if not fqns:
        return []
    query = """
    MATCH (anchor:Table)-[:JOINS_TO*1..2]->(hub:Table)
    WHERE anchor.fqn IN $fqns
      AND hub.is_dimension_hub = true
      AND NOT hub.fqn IN $fqns
    WITH hub, count(DISTINCT anchor.fqn) AS reachable
    WHERE reachable >= 2
    RETURN hub.fqn AS fqn, hub.hub_join_col AS hub_join_col,
           hub.in_degree AS in_degree, reachable
    ORDER BY reachable DESC, hub.in_degree DESC
    LIMIT 3
    """
    t0 = time.monotonic()
    try:
        results = _neo4j_run(query, {"fqns": fqns})
        logger.debug("neo4j | fn=find_common_dimension_hub | ms={:.0f} | hits={}", (time.monotonic() - t0) * 1000, len(results))
        return [dict(r) for r in results]
    except Exception as e:
        logger.warning("neo4j | find_common_dimension_hub failed | error={}", e)
        return []

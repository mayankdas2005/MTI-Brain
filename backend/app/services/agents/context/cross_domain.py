"""Cross-domain detection — 4-method cascade to find the conformed dimension hub.

CRITICAL: Use hub_table_fqn (the conformed dimension) from BRIDGES_TO.
          NOT bridge_table_fqn (the table that bridges two communities).
          These are different properties set by different ingestion passes.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents import neo4j_client


def detect(tables: list[dict]) -> tuple[list[dict], dict | None, bool]:
    """Detect cross-domain queries and identify the conformed dimension hub.

    Returns: (tables_with_hub_added, hub_info, is_cross_domain)

    4-method cascade:
    1. business_domain diversity — most reliable (doesn't need community_id)
    2. BRIDGES_TO hub lookup — best join path info
    3. Existing is_dimension_hub in anchor tables
    4. Graph traversal for common reachable hub
    """
    is_cross_domain = False
    hub_info: dict | None = None

    # Method 1: business_domain diversity (primary — works regardless of Leiden clustering)
    domains = {t.get("business_domain") for t in tables if t.get("business_domain")}
    if len(domains) > 1:
        is_cross_domain = True
        logger.info("cross_domain | detected | method=business_domain | domains={}", domains)

    # Method 1b: community_id diversity (fallback when business_domain is sparse on some tenants)
    if not is_cross_domain:
        communities = {t.get("community_id") for t in tables if t.get("community_id") is not None}
        if len(communities) > 1:
            is_cross_domain = True
            logger.info("cross_domain | detected | method=community_diversity | communities={}", communities)

    # Method 2: BRIDGES_TO hub — only useful if _pass_bridges_to_enhanced() ran
    community_ids = list({t.get("community_id") for t in tables if t.get("community_id") is not None})
    if len(set(community_ids)) > 1:
        try:
            hub_results = neo4j_client.get_dimension_hub_for_communities(community_ids)
            if hub_results:
                h = hub_results[0] if isinstance(hub_results, list) else hub_results
                if h.get("hub_table_fqn"):   # guard: only set if fqn is actually present
                    hub_info = {
                        "hub_table_fqn": h.get("hub_table_fqn"),   # conformed dimension
                        "hub_join_col":  h.get("hub_join_col", "code"),
                        "join_safe":     h.get("join_safe", True),
                    }
                    is_cross_domain = True
                    logger.info("cross_domain | hub_found | method=bridges_to | hub={}", hub_info["hub_table_fqn"])
        except Exception as e:
            logger.warning("cross_domain | bridges_to failed | error={}", e)

    # Method 3: Dimension hub already in anchor tables
    if is_cross_domain and not hub_info:
        for t in sorted(tables, key=lambda x: x.get("in_degree", 0) or 0, reverse=True):
            if t.get("is_dimension_hub") and t.get("hub_join_col"):
                hub_info = {
                    "hub_table_fqn": t["fqn"],
                    "hub_join_col":  t["hub_join_col"],
                    "join_safe": True,
                }
                logger.info("cross_domain | hub_found | method=anchor_hub | hub={}", t["fqn"])
                break

    # Method 4: Graph traversal — find hub reachable from multiple anchor tables
    if is_cross_domain and not hub_info:
        fqns = [t["fqn"] for t in tables if t.get("fqn")]
        try:
            hubs = neo4j_client.find_common_dimension_hub(fqns)
            if hubs:
                hub_info = {
                    "hub_table_fqn": hubs[0]["fqn"],
                    "hub_join_col":  hubs[0].get("hub_join_col") or "code",
                    "join_safe": True,
                }
                logger.info("cross_domain | hub_found | method=graph_traversal | hub={}", hubs[0]["fqn"])
        except Exception as e:
            logger.warning("cross_domain | graph_traversal failed | error={}", e)

    if is_cross_domain and not hub_info:
        logger.warning("cross_domain | detected but no hub found | domains={}", domains)

    # Add hub table to anchor tables if found and not already present
    if hub_info and hub_info.get("hub_table_fqn"):
        hub_fqn = hub_info["hub_table_fqn"]
        existing_fqns = {t["fqn"] for t in tables if t.get("fqn")}
        if hub_fqn not in existing_fqns:
            try:
                rows = neo4j_client.get_tables_with_context([hub_fqn])
                if rows:
                    hub_tbl = rows[0].get("t") or {}
                    hub_tbl["retrieval_paths"] = ["cross_domain_hub"]
                    tables = list(tables) + [hub_tbl]
                    logger.info("cross_domain | hub_table_added | fqn={} | join_col={}", hub_fqn, hub_info["hub_join_col"])
            except Exception as e:
                logger.warning("cross_domain | hub_table_add failed | error={}", e)

    return tables, hub_info, is_cross_domain

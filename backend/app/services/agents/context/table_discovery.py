"""8-path parallel table discovery — pure Neo4j, no templates in discovery paths."""

from __future__ import annotations

import asyncio

from app.core.logger import logger
from app.core.retry import retry_async, retry_sync
from app.services.agents import neo4j_client


async def _empty_coroutine() -> list:
    return []


async def _run_path(fn, *args) -> list:
    """Run a Neo4j search fn in a thread with transient-error retry."""
    return await retry_async(lambda: asyncio.to_thread(fn, *args), service="neo4j")

# Path weights for merge scoring — templates are NOT a discovery path
_PATH_WEIGHTS = {
    "direct_vector":     1.00,
    "direct_fts":        0.90,
    "intent":            0.80,
    "businessterm":      0.85,
    "column_search":     0.75,
    "community":         0.70,
    "domain":            0.80,
    "domain_fallback":   0.60,   # domain path when not explicitly detected — lower confidence
    "query_pattern":     1.20,   # ground truth — highest weight
    "query_template":    0.95,   # REQUIRES_TABLE structural hint
    "joinpath":          0.65,   # expansion pass
    "entity_value":      0.90,   # direct alias/vocabulary match — strong entity signal
}

_MAX_ANCHOR_TABLES = 14   # increased from 8 — pinned entity tables don't count against this


def _safe(result) -> list[dict]:
    """Return empty list if result is an exception or non-list."""
    if isinstance(result, Exception):
        return []
    return result or []


async def run_8_path_discovery(
    embedding: list[float],
    tokens: list[str],
    question: str,
    domain_detected: bool,
) -> tuple[list[dict], list[str], list[dict], list[str], list[str]]:
    """Run all discovery paths in parallel.

    Returns: (tables, entity_pinned_fqns, business_term_hits, intent_table_fqns, domain_table_fqns)
      - tables: merged and ranked table list
      - entity_pinned_fqns: FQNs that must survive the MAX cap (entity + BT related)
      - business_term_hits: raw BT results for downstream (filter hints etc.)
      - intent_table_fqns: FQNs found via RELEVANT_TO intent path (pre-merge, for anchor injection)
      - domain_table_fqns: FQNs found via BELONGS_TO domain path (pre-merge, for anchor injection)

    Any path that errors returns [] — pipeline continues with remaining paths.
    """
    search_query = " ".join(tokens[:50])  # increased from 30 to cover longer questions

    tasks = [
        _run_path(neo4j_client.search_tables_vector, embedding),             # 0: direct_vector
        _run_path(neo4j_client.search_tables_fulltext, search_query),        # 1: direct_fts
        _run_path(neo4j_client.search_tables_via_intents, embedding),        # 2: intent (RELEVANT_TO)
        _run_path(neo4j_client.search_tables_via_business_terms, embedding, search_query),  # 3: businessterm
        _run_path(neo4j_client.search_tables_via_columns, embedding, search_query),         # 4: column_search
        _run_path(neo4j_client.search_tables_via_community, embedding),      # 5: community
        _run_path(neo4j_client.search_tables_via_domain, embedding),         # 6: domain (always runs now)
        _run_path(neo4j_client.search_tables_from_query_patterns, embedding),# 7: query_pattern
        _run_path(neo4j_client.search_tables_via_query_templates, embedding),# 8: query_template (REQUIRES_TABLE)
        _run_path(neo4j_client.search_tables_via_filter_values, tokens),     # 9: entity_value match
        _run_path(neo4j_client.get_business_terms_with_related_tables, embedding, search_query),  # 10: BT pinning data
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    (
        tables_direct_v, tables_direct_fts,
        tables_via_intent, tables_via_bterm, tables_via_columns,
        tables_via_comm, tables_via_domain, tables_via_patterns,
        tables_via_templates, tables_via_entity,
        bt_pin_data,
    ) = [_safe(r) for r in results]

    # Determine domain path weight based on whether it was explicitly detected
    domain_path_name = "domain" if domain_detected else "domain_fallback"

    path_names = [
        "direct_vector", "direct_fts", "intent", "businessterm",
        "column_search", "community", domain_path_name, "query_pattern",
        "query_template", "entity_value",
    ]
    path_results = [
        tables_direct_v, tables_direct_fts, tables_via_intent, tables_via_bterm,
        tables_via_columns, tables_via_comm, tables_via_domain, tables_via_patterns,
        tables_via_templates, tables_via_entity,
    ]

    for name, result in zip(path_names, path_results):
        if result:
            logger.info("context_fetcher | path={} | tables={}", name, [t.get("fqn") for t in result])

    # Collect entity-pinned FQNs BEFORE merge — these survive the MAX cap
    pinned_fqns: set[str] = set()

    # Pin 1: entity_value path — table directly holds the user's entity value.
    # Only pin on real value matches (score >= 2: distinct_values or value_aliases).
    # score=1 means the token only appeared in a column *description* — too weak to force-pin.
    for t in tables_via_entity:
        if t.get("fqn") and (t.get("entity_match_score") or 0) >= 2:
            pinned_fqns.add(t["fqn"])

    # Pin 2: BusinessTerm related_table_fqns where score >= 0.72
    for bt in bt_pin_data:
        if bt.get("score", 0) >= 0.72:
            for fqn in bt.get("related_table_fqns") or []:
                if fqn:
                    pinned_fqns.add(fqn)

    if pinned_fqns:
        logger.info("context_fetcher | pinned_fqns | count={} | fqns={}", len(pinned_fqns), list(pinned_fqns))

    # Extract intent/domain FQNs BEFORE merge — used by anchor_resolver for deterministic injection
    intent_table_fqns = [t["fqn"] for t in tables_via_intent if t.get("fqn")]
    domain_table_fqns = [t["fqn"] for t in tables_via_domain if t.get("fqn")]

    tables = _merge_table_sources(dict(zip(path_names, path_results)), pinned_fqns)
    logger.info("context_fetcher | merged_tables | fqns={}", [t.get("fqn") for t in tables])

    # JoinPath expansion — adds directly-connected tables not found by main paths
    semantic_fqns = {t["fqn"] for t in tables if t.get("fqn")}
    try:
        tables_via_joins = retry_sync(
            lambda: neo4j_client.search_tables_via_joinpaths(list(semantic_fqns)),
            service="neo4j",
        )
        logger.info("context_fetcher | path=joinpath_expansion | new={}",
                    [(t.get("fqn"), t.get("matched_via")) for t in tables_via_joins])
        existing = set(semantic_fqns)
        for t in tables_via_joins:
            if t.get("fqn") and t["fqn"] not in existing:
                t["retrieval_paths"] = ["joinpath"]
                tables.append(t)
                existing.add(t["fqn"])
            # Add intermediate bridge tables (path_tables[1:-1]) needed for multi-hop JOINs
            for bridge_fqn in (t.get("path_tables") or [])[1:-1]:
                if bridge_fqn and bridge_fqn not in existing:
                    try:
                        bridge_rows = retry_sync(
                            lambda _f=bridge_fqn: neo4j_client.get_tables_with_context([_f]),
                            service="neo4j",
                        )
                        if bridge_rows:
                            bt = dict(bridge_rows[0].get("t") or {})
                            if bt.get("fqn"):
                                bt["retrieval_paths"] = ["bridge_table"]
                                tables.append(bt)
                                existing.add(bridge_fqn)
                                logger.info("context_fetcher | bridge_table | fqn={}", bridge_fqn)
                    except Exception as _e:
                        logger.warning("context_fetcher | bridge_table_add_failed | fqn={} | error={}", bridge_fqn, _e)
    except Exception as e:
        logger.warning("context_fetcher | joinpath_expansion failed | error={}", e)

    return tables, list(pinned_fqns), bt_pin_data, intent_table_fqns, domain_table_fqns


def _merge_table_sources(
    sources: dict[str, list[dict]],
    pinned_fqns: set[str],
) -> list[dict]:
    """Merge all path results into a ranked, deduplicated table list.

    Scoring:
    - weighted_score = max(prev_score, new_score × path_weight) + 0.05 per additional source
    - Bonus: +0.10 if is_dimension_hub=True
    - Sort: source_count DESC, score DESC, pagerank_score DESC

    Pinning: entity-matched tables (pinned_fqns) are guaranteed to survive the
    _MAX_ANCHOR_TABLES cap. They are partitioned out before the cap is applied.
    """
    seen: dict[str, dict] = {}
    for path_name, table_list in sources.items():
        weight = _PATH_WEIGHTS.get(path_name, 0.75)
        for t in table_list:
            fqn = t.get("fqn")
            if not fqn:
                continue
            raw_score = (t.get("score") or 0.0) * weight
            if fqn not in seen:
                entry = dict(t)
                entry["retrieval_paths"] = [path_name]
                entry["_weighted_score"] = raw_score
                seen[fqn] = entry
            else:
                seen[fqn]["retrieval_paths"].append(path_name)
                cur = seen[fqn].get("_weighted_score", 0.0)
                seen[fqn]["_weighted_score"] = max(cur, raw_score) + 0.05

    # Apply hub bonus
    for entry in seen.values():
        if entry.get("is_dimension_hub"):
            entry["_weighted_score"] = entry.get("_weighted_score", 0.0) + 0.10

    # Sort: source_count DESC, weighted_score DESC, pagerank_score DESC
    merged = sorted(
        seen.values(),
        key=lambda x: (
            len(x.get("retrieval_paths") or []),
            x.get("_weighted_score", 0.0),
            x.get("pagerank_score", 0.0),
        ),
        reverse=True,
    )

    # Partition: pinned tables always survive, non-pinned fill remaining slots
    pinned = [e for e in merged if e.get("fqn") in pinned_fqns]
    non_pinned = [e for e in merged if e.get("fqn") not in pinned_fqns]
    result = pinned + non_pinned[:max(_MAX_ANCHOR_TABLES - len(pinned), 4)]

    return result


def merge_template_results(vector_results: list[dict], fts_results: list[dict]) -> list[dict]:
    """Merge QueryTemplate results for hints-only display."""
    seen: dict[str, dict] = {}
    for t in vector_results:
        tid = t.get("id")
        if tid:
            seen[tid] = dict(t)
    for t in fts_results:
        tid = t.get("id")
        if not tid:
            continue
        if tid not in seen:
            seen[tid] = dict(t)
        else:
            seen[tid]["score"] = max(seen[tid].get("score") or 0.0, t.get("score") or 0.0) + 0.05
    return sorted(seen.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:5]


def merge_business_terms(vector_results: list[dict], fts_results: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for bt in vector_results:
        term = bt.get("term")
        if term:
            seen[term] = dict(bt)
    for bt in fts_results:
        term = bt.get("term")
        if not term:
            continue
        if term not in seen:
            seen[term] = dict(bt)
        else:
            seen[term]["score"] = max(seen[term].get("score") or 0.0, bt.get("score") or 0.0) + 0.05
    return sorted(seen.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:5]

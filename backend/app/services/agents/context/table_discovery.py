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


def _merge_by_fqn_max_score(existing: list[dict], new_results: list[dict]) -> list[dict]:
    """Merge two table lists by fqn, keeping the highest score per fqn."""
    seen: dict[str, dict] = {t["fqn"]: dict(t) for t in existing if t.get("fqn")}
    for t in new_results:
        fqn = t.get("fqn")
        if not fqn:
            continue
        if fqn not in seen or (t.get("score") or 0) > (seen[fqn].get("score") or 0):
            seen[fqn] = dict(t)
    return list(seen.values())


async def run_8_path_discovery(
    embedding: list[float],
    tokens: list[str],
    question: str,
    domain_detected: bool,
    entity_tokens: list[str] | None = None,
    search_term_embeds: list[list[float]] | None = None,
    search_terms: list[str] | None = None,
) -> tuple[list[dict], list[str], list[dict], list[str], list[str]]:
    """Run all discovery paths in parallel.

    Returns: (tables, entity_pinned_fqns, business_term_hits, intent_table_fqns, domain_table_fqns)
      - tables: merged and ranked table list
      - entity_pinned_fqns: FQNs that must survive the MAX cap (entity + BT related)
      - business_term_hits: raw BT results for downstream (filter hints etc.)
      - intent_table_fqns: FQNs found via RELEVANT_TO intent path (pre-merge, for anchor injection)
      - domain_table_fqns: FQNs found via BELONGS_TO domain path (pre-merge, for anchor injection)

    Any path that errors returns [] — pipeline continues with remaining paths.

    Multi-term discovery (Layer 2):
      Paths 0,1,3,4,10 are run once more per focused search_term using per-term embeddings
      and per-term FTS queries. Results are fed to _merge_table_sources() under
      focused_vector_{i} / focused_fts_{i} / focused_bt_{i} / focused_col_{i} keys so that
      source_count correctly reflects how many distinct (path×term) combinations found each table.
      Tables found by full_question + all 3 focused terms accumulate source_count=4+, while
      spurious tables found by only 1 focused term have source_count=1. The existing
      source_count-first sort in _merge_table_sources() is the selection mechanism.
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
        _run_path(neo4j_client.search_tables_via_filter_values, tokens),     # 9: entity_value match (uses search_variants tokens)
        _run_path(neo4j_client.get_business_terms_with_related_tables, embedding, search_query),  # 10: BT pinning data
    ]

    # Focused-term tasks: paths 0,1,3,4,10 run for each search_term in parallel
    # Each (path, term) pair gets its own key so source_count increments per term that finds the table.
    focused_tasks: list = []
    focused_keys: list[str] = []
    for i, (term_emb, term_str) in enumerate(
        zip(search_term_embeds or [], search_terms or [])
    ):
        fts_query = term_str  # short phrase — no tokenization needed
        focused_tasks.extend([
            _run_path(neo4j_client.search_tables_vector, term_emb),
            _run_path(neo4j_client.search_tables_fulltext, fts_query),
            _run_path(neo4j_client.search_tables_via_business_terms, term_emb, fts_query),
            _run_path(neo4j_client.search_tables_via_columns, term_emb, fts_query),
            _run_path(neo4j_client.get_business_terms_with_related_tables, term_emb, fts_query),
        ])
        focused_keys.extend([
            f"focused_vector_{i}", f"focused_fts_{i}",
            f"focused_bt_{i}", f"focused_col_{i}", f"focused_btpin_{i}",
        ])

    all_tasks = tasks + focused_tasks
    all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    results = all_results[:len(tasks)]
    focused_results = all_results[len(tasks):]

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

    # Append focused-term results with per-term path weights
    for key, result in zip(focused_keys, focused_results):
        safe_result = _safe(result)
        if safe_result:
            path_names.append(key)
            path_results.append(safe_result)
            # Register weight for _merge_table_sources
            if key.startswith("focused_vector"):
                _PATH_WEIGHTS[key] = 0.85
            elif key.startswith("focused_fts"):
                _PATH_WEIGHTS[key] = 0.80
            elif key.startswith("focused_bt"):
                _PATH_WEIGHTS[key] = 0.82
            elif key.startswith("focused_col"):
                _PATH_WEIGHTS[key] = 0.78
            elif key.startswith("focused_btpin"):
                _PATH_WEIGHTS[key] = 0.82

    for name, result in zip(path_names, path_results):
        if result:
            logger.info("context_fetcher | path={} | tables={}", name, [t.get("fqn") for t in result])

    # Collect entity-pinned FQNs BEFORE merge — these survive the MAX cap
    pinned_fqns: set[str] = set()

    # Pin 1: entity_value path — table directly holds the user's entity value.
    # Only pin on real value matches (score >= 2: distinct_values or value_aliases).
    # score=1 means the token only appeared in a column *description* — too weak to force-pin.
    pin1_fqns: set[str] = set()
    for t in tables_via_entity:
        if t.get("fqn") and (t.get("entity_match_score") or 0) >= 2:
            pin1_fqns.add(t["fqn"])
    pinned_fqns |= pin1_fqns
    if pin1_fqns:
        logger.info("context_fetcher | pinned_pin1_entity_value | fqns={}", sorted(pin1_fqns))

    # Pin 2: BusinessTerm related_table_fqns where score >= 0.72
    # Also accumulate bt_pin_data from focused_btpin paths so focused-term BT hits contribute to pinning.
    for key, result in zip(focused_keys, focused_results):
        if key.startswith("focused_btpin"):
            bt_pin_data = _merge_by_fqn_max_score(bt_pin_data, _safe(result))

    pin2_fqns: set[str] = set()
    for bt in bt_pin_data:
        if bt.get("score", 0) >= 0.72:
            for fqn in bt.get("related_table_fqns") or []:
                if fqn:
                    pin2_fqns.add(fqn)
    pinned_fqns |= pin2_fqns
    if pin2_fqns:
        logger.info("context_fetcher | pinned_pin2_businessterm | fqns={}", sorted(pin2_fqns))

    # Pin 3: high-score query_template hit → structural ground truth
    pin3_fqns: set[str] = set()
    for entry in tables_via_templates:
        if (entry.get("score") or 0) >= 0.75 and entry.get("fqn"):
            pin3_fqns.add(entry["fqn"])
            logger.info("context_fetcher | pinned_pin3_query_template | fqn={} | score={:.3f}",
                        entry["fqn"], entry.get("score", 0))
    pinned_fqns |= pin3_fqns

    # Pin 4: per-entity FTS — run a secondary gather for each entity token
    entity_pinned_fqns: set[str] = set()
    if entity_tokens:
        ent_tasks = []
        for ent in entity_tokens[:4]:
            ent_tasks.append(_run_path(neo4j_client.search_tables_via_business_terms, embedding, ent))
            ent_tasks.append(_run_path(neo4j_client.search_tables_fulltext, ent))
            ent_tasks.append(_run_path(neo4j_client.lookup_business_terms, [ent]))
        ent_results = await asyncio.gather(*ent_tasks, return_exceptions=True)

        for i, ent in enumerate(entity_tokens[:4]):
            bt_res   = _safe(ent_results[i * 3])
            fts_res  = _safe(ent_results[i * 3 + 1])
            bt_exact = _safe(ent_results[i * 3 + 2])

            for t in bt_res + fts_res:
                if t.get("fqn") and (t.get("score") or 0) >= 0.5:
                    entity_pinned_fqns.add(t["fqn"])
            for bt in bt_exact:
                for fqn in (bt.get("related_table_fqns") or []):
                    if fqn:
                        entity_pinned_fqns.add(fqn)

            if bt_res:
                logger.info("context_fetcher | entity_bt_fts | entity={} | tables={}",
                            ent, [t.get("fqn") for t in bt_res])
                tables_via_bterm = _merge_by_fqn_max_score(tables_via_bterm, bt_res)
            if fts_res:
                logger.info("context_fetcher | entity_table_fts | entity={} | tables={}",
                            ent, [t.get("fqn") for t in fts_res])
                tables_direct_fts = _merge_by_fqn_max_score(tables_direct_fts, fts_res)
            if bt_exact:
                exact_fqns = [fqn for bt in bt_exact for fqn in (bt.get("related_table_fqns") or []) if fqn]
                if exact_fqns:
                    logger.info("context_fetcher | entity_bt_exact | entity={} | pinned_tables={}",
                                ent, exact_fqns)

    pinned_fqns |= entity_pinned_fqns
    if entity_pinned_fqns:
        logger.info("context_fetcher | pinned_pin4_entity_fts | fqns={}", sorted(entity_pinned_fqns))

    # Pin 5: intent path — RELEVANT_TO edge is a strong structural signal
    pin5_fqns: set[str] = set()
    for t in tables_via_intent:
        if (t.get("score") or 0) >= 0.75 and t.get("fqn"):
            pin5_fqns.add(t["fqn"])
    pinned_fqns |= pin5_fqns
    if pin5_fqns:
        logger.info("context_fetcher | pinned_pin5_intent | fqns={}", sorted(pin5_fqns))

    # Pin 6: domain path — BELONGS_TO is a strong structural signal
    pin6_fqns: set[str] = set()
    for t in tables_via_domain:
        if (t.get("score") or 0) >= 0.75 and t.get("fqn"):
            pin6_fqns.add(t["fqn"])
    pinned_fqns |= pin6_fqns
    if pin6_fqns:
        logger.info("context_fetcher | pinned_pin6_domain | fqns={}", sorted(pin6_fqns))

    if pinned_fqns:
        logger.info("context_fetcher | pinned_total | count={} | all_pinned={}", len(pinned_fqns), sorted(pinned_fqns))

    # Extract intent/domain FQNs BEFORE merge — used by anchor_resolver for deterministic injection
    intent_table_fqns = [t["fqn"] for t in tables_via_intent if t.get("fqn")]
    domain_table_fqns = [t["fqn"] for t in tables_via_domain if t.get("fqn")]

    tables = _merge_table_sources(dict(zip(path_names, path_results)), pinned_fqns)
    logger.info("context_fetcher | merged_tables_final | count={} | fqns={}", len(tables), [t.get("fqn") for t in tables])

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

    return tables, list(pinned_fqns), bt_pin_data, intent_table_fqns, domain_table_fqns, list(entity_pinned_fqns)


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

    # Log per-table scores (top 25) for debugging
    for entry in merged[:25]:
        logger.debug(
            "context_fetcher | table_score | fqn={} | source_count={} | paths={} | score={:.3f}",
            entry.get("fqn"), len(entry.get("retrieval_paths") or []),
            sorted(entry.get("retrieval_paths") or []), entry.get("_weighted_score", 0),
        )

    # Partition: pinned tables always survive, non-pinned fill remaining slots
    pinned = [e for e in merged if e.get("fqn") in pinned_fqns]
    non_pinned = [e for e in merged if e.get("fqn") not in pinned_fqns]
    cap_slots = max(_MAX_ANCHOR_TABLES - len(pinned), 4)
    selected_non_pinned = non_pinned[:cap_slots]
    dropped = [e["fqn"] for e in non_pinned[cap_slots:]]
    if dropped:
        logger.info("context_fetcher | tables_dropped_at_cap | cap={} | dropped={}", _MAX_ANCHOR_TABLES, dropped)
    logger.info("context_fetcher | tables_non_pinned | count={} | cap_slots_remaining={}",
                len(non_pinned), cap_slots)
    result = pinned + selected_non_pinned

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

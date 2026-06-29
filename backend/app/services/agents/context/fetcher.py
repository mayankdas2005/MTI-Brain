"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. No Redshift. Embeds the question, runs 8-path table discovery,
detects cross-domain queries, loads columns with join-critical prioritization,
and assembles SemanticContext for downstream agents.

Post-discovery work is split into two parallel groups:
  Group A — independent of tables: templates, business terms, intents, memory
  Group B — depends on tables: cross-domain, join-critical cols, column loading

Both groups run concurrently via asyncio.gather().  All sync Neo4j calls inside
Group B are wrapped in asyncio.to_thread() so they never block the event loop.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.core.retry import retry_async, retry_sync
from app.services.agents import neo4j_client
from app.services.agents.helpers import merge_neo4j_raw_graph
from app.services.agents.state import AnalyticsState
from . import helpers, table_discovery, column_loader, cross_domain


def _build_context_label(semantic_context: dict) -> str:
    lines = []

    tables = semantic_context.get("tables") or []
    table_fqns = [t["fqn"] for t in tables if t.get("fqn")]
    n_tables = len(table_fqns)
    if n_tables:
        fqn_preview = ", ".join(table_fqns[:5])
        suffix = f" + {n_tables - 5} more" if n_tables > 5 else ""
        lines.append(f"- **Tables found:** {n_tables} — {fqn_preview}{suffix}")
    else:
        lines.append("- **Tables found:** 0 — no tables matched")

    col_lookup = semantic_context.get("_column_lookup") or {}
    n_cols = len(col_lookup)
    n_with_values = sum(
        1 for v in col_lookup.values()
        if v.get("distinct_values") or v.get("sample_values")
    )
    lines.append(f"- **Columns loaded:** {n_cols} ({n_with_values} with known values)")

    domains = list(dict.fromkeys(t.get("business_domain") for t in tables if t.get("business_domain")))
    if domains:
        cross_tag = " (cross-domain)" if semantic_context.get("is_cross_domain") else ""
        lines.append(f"- **Domain:** {', '.join(domains[:2])}{cross_tag}")

    business_terms = semantic_context.get("business_terms") or []
    terms = [bt.get("term", "") for bt in business_terms[:3] if bt.get("term")]
    if terms:
        lines.append(f"- **Business terms:** {', '.join(repr(t) for t in terms)}")

    entity_hints = semantic_context.get("entity_hints") or []
    hint_strs = [
        f'"{eh.get("token")}" → {eh.get("table_fqn")}.{eh.get("column")}'
        for eh in entity_hints[:3]
        if eh.get("token") and eh.get("table_fqn") and eh.get("column")
    ]
    if hint_strs:
        lines.append(f"- **Entity hints:** {', '.join(hint_strs)}")

    intents = semantic_context.get("intents") or []
    if intents and intents[0].get("name"):
        lines.append(f"- **Query intent:** {intents[0]['name']}")

    _pattern = semantic_context.get("_matched_pattern")
    _tier = semantic_context.get("_matched_pattern_tier")
    if _pattern and _tier:
        _raw = _pattern.get("raw_score", 0) or 0
        _q = (_pattern.get("question_text") or "")[:60]
        lines.append(f"- **Prior pattern:** {_tier} match ({_raw:.2f}) — \"{_q}\"")

    if semantic_context.get("is_followup"):
        lines.append("- **Follow-up detected**")

    return "\n".join(lines)


async def context_fetcher(state: AnalyticsState, config: RunnableConfig) -> dict:
    entity_tokens = state.get("entity_tokens") or None
    # search_variants are corrected/expanded entity tokens from intake_classifier (abbrev expansions,
    # typo fixes). They replace raw entity_tokens everywhere in discovery + downstream prompts.
    search_variants = state.get("search_variants") or entity_tokens or None
    search_terms    = state.get("search_terms") or []
    logger.info(
        "context_fetcher START | thread={} | question={} | entity_tokens={} | search_variants={} | search_terms={}",
        state["thread_id"], state["question"][:80], entity_tokens, search_variants, search_terms,
    )

    try:
        # ── Short-term memory + follow-up detection ────────────────────────────
        _conv_history = state.get("conversation_history") or ""
        session_summary = _conv_history if _conv_history and _conv_history != "(no prior context)" else (state.get("summary") or "")
        raw_question    = state["question"]
        is_followup     = helpers.is_followup_question(raw_question, bool(session_summary))

        if is_followup:
            previous_follow_ups = state.get("follow_ups") or []
            search_query = helpers.reconstruct_question(
                raw_question, session_summary or "", previous_follow_ups
            )
            effective_question = search_query
            logger.info("context_fetcher | follow-up reconstructed | search={}", search_query[:80])
        elif state.get("is_refinement") and state.get("prior_question"):
            prior_q = (state["prior_question"] or "").strip()
            search_query = f"{prior_q} {raw_question}"
            effective_question = f"{prior_q}\n\nUser refinement: {raw_question.strip()}"
            logger.info("context_fetcher | refinement search | combined_query={}", search_query[:120])
        else:
            search_query = raw_question
            effective_question = raw_question

        # ── Embed question + focused search_terms in one Cohere batch call ────
        # all_to_embed[0] = full question (unchanged path for existing discovery logic)
        # all_to_embed[1:] = focused 2-4 word phrases from intake_classifier (Layer 2)
        all_to_embed = [search_query] + list(search_terms)
        all_embeddings = await retry_async(
            lambda: helpers.get_embeddings_batch(all_to_embed), service="redis"
        )
        embedding           = all_embeddings[0] if all_embeddings else []
        search_term_embeds  = all_embeddings[1:] if len(all_embeddings) > 1 else []
        logger.debug(
            "context_fetcher | batch_embed | total_texts={} | term_embeds={}",
            len(all_to_embed), len(search_term_embeds),
        )

        tokens    = helpers.tokenize_with_bigrams(search_query)
        domain_detected = helpers.domain_keyword_detected(search_query)

        # ── Table discovery: all paths in parallel ────────────────────────────
        # Returns (tables, pinned_fqns, bt_pin_data, intent_table_fqns, domain_table_fqns, entity_pinned_fqns)
        # Pass search_variants as entity_tokens so corrected/expanded tokens reach all discovery paths.
        tables, pinned_fqns, _bt_pin_data, intent_table_fqns, domain_table_fqns, entity_pinned_fqns = \
            await table_discovery.run_8_path_discovery(
                embedding, tokens, search_query, domain_detected,
                entity_tokens=search_variants,
                search_term_embeds=search_term_embeds,
                search_terms=list(search_terms),
            )

        if not tables:
            logger.warning("context_fetcher | NO tables found | thread={}", state["thread_id"])
            return {"error": "semantic_layer_unavailable", "semantic_context": None}

        # K2: canonical domain direct lookup for multi-domain queries (Q3 pattern)
        # Extracts DOMAIN lines from query_intent and runs a targeted BELONGS_TO Cypher query.
        # Guarantees domain coverage even when vector search misses a domain's primary tables.
        _canonical_domains = [
            l.split(":", 1)[1].strip()
            for l in (state.get("query_intent") or [])
            if l.startswith("DOMAIN:")
        ]
        if _canonical_domains:
            try:
                _domain_rows = await asyncio.to_thread(
                    neo4j_client.get_tables_for_canonical_domains, _canonical_domains
                )
                _domain_fqns = [r["fqn"] for r in _domain_rows if r.get("fqn")]
                if _domain_fqns:
                    _existing_fqns = {f for f in domain_table_fqns}
                    _new_fqns = [f for f in _domain_fqns if f not in _existing_fqns]
                    domain_table_fqns = list(domain_table_fqns) + _new_fqns
                    logger.info(
                        "context_fetcher | canonical_domain_lookup | domains={} | new_fqns={} | thread={}",
                        _canonical_domains, _new_fqns, state["thread_id"],
                    )
            except Exception as _e:
                logger.warning("context_fetcher | canonical_domain_lookup failed | error={}", _e)

        # ── Candidate column summary — batch Neo4j call across all discovered tables ──
        # Lightweight: returns measure_cols and date_cols names only (no cardinality).
        # Used by anchor_resolver._inject_signal_tables() to gate tables that add no
        # unique measures/dates when the primary fact table already covers them.
        _all_candidate_fqns = [t["fqn"] for t in tables if t.get("fqn")]
        try:
            candidate_col_summary = await asyncio.to_thread(
                neo4j_client.get_candidate_col_summary, _all_candidate_fqns
            )
            _measure_count = sum(1 for v in candidate_col_summary.values() if v.get("measure_cols"))
            _date_count    = sum(1 for v in candidate_col_summary.values() if v.get("date_cols"))
            logger.info(
                "context_fetcher | candidate_col_summary | tables={} | with_measures={} | with_dates={}",
                len(candidate_col_summary), _measure_count, _date_count,
            )
        except Exception as _e:
            logger.warning("context_fetcher | candidate_col_summary failed | error={} — skipping", _e)
            candidate_col_summary = {}

        # ── Groups A + B run in parallel ───────────────────────────────────────
        # Group A: independent of table results — templates, terms, intents
        # Group B: depends on tables — cross-domain, join-critical cols, column loading
        # memory_context comes from lt_memory_retriever node (ran before context_fetcher)
        memory_context = state.get("lt_memory_context") or ""
        (group_a, group_b) = await asyncio.gather(
            _fetch_group_a(embedding, search_query),
            _fetch_group_b(tables, embedding, search_query, entity_tokens=search_variants),
        )

        templates_merged, business_terms, intents = group_a
        tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints, entity_col_tables = group_b

        # ── Consensus tables: found by 4+ independent discovery paths ─────────
        # Computed before trim_objects which may strip retrieval_paths metadata.
        # Used by anchor_resolver Signal 4 as a fallback when intent/business-term
        # signals don't fire (e.g. Neo4j data gaps). A table in 4+ paths has strong
        # multi-signal agreement; a false positive from entity_value alone has 1 path.
        consensus_table_fqns = [
            t["fqn"] for t in tables
            if t.get("fqn") and len(t.get("retrieval_paths") or []) >= 4
        ]

        # ── Trim for LLM display (join-critical gets full descriptions) ────────
        templates_trimmed = helpers.trim_objects(templates_merged)
        tables_trimmed    = helpers.trim_objects(tables)
        columns_trimmed   = helpers.trim_objects(display_columns, join_critical_cols=join_crit_cols)

        # ── Always inject lpp.fx_rate at position 0 — it lives in fx_and_hedging
        #    domain so it is never discovered by cash/liquidity vector search.
        #    Must be first so it is never truncated in the anchor resolver prompt.
        _FX_FQN = "lpp.fx_rate"
        if not any(t.get("fqn") == _FX_FQN for t in tables_trimmed):
            tables_trimmed = [{
                "fqn":             _FX_FQN,
                "name":            "fx_rate",
                "schema":          "lpp",
                "business_domain": "fx_and_hedging",
                "description":     (
                    "Daily foreign exchange rates between currency pairs "
                    "(Bloomberg, ECB, internal sources). "
                    "Use to convert multi-currency financial amounts to USD. "
                    "Both directions stored: base=BRL/quote=USD and base=USD/quote=BRL. "
                    "USD/USD row exists (rate=1.0) so USD accounts need no special handling."
                ),
                "grain":           "One row per currency pair, rate date, and rate type combination.",
                "natural_measures": ["rate"],
                "natural_dimensions": [
                    "base_currency", "quote_currency", "rate_date", "rate_type", "source",
                ],
                "row_count":       730475,
                "column_count":    7,
                "is_time_series":  False,
                "is_dimension_hub": False,
            }] + list(tables_trimmed)
            logger.debug("context_fetcher | fx_rate_injected | thread={}", state["thread_id"])

        # ── Assemble SemanticContext ───────────────────────────────────────────
        semantic_context = {
            "templates":             templates_trimmed,
            "tables":                tables_trimmed,
            "columns":               columns_trimmed,
            "_column_lookup":        col_lookup,
            "join_critical_cols":    list(join_crit_cols),
            "business_terms":        business_terms,
            "intents":               intents,
            "is_cross_domain":       is_cross_domain,
            "cross_domain_hub":      hub_info,
            "session_summary":       session_summary,
            "memory_context":        memory_context,
            "effective_question":    effective_question,
            "is_followup":           is_followup,
            "entity_hints":          entity_hints,
            "intent_table_fqns":     intent_table_fqns,
            "domain_table_fqns":     domain_table_fqns,
            "consensus_table_fqns":  consensus_table_fqns,
            "entity_pinned_fqns":    set(entity_pinned_fqns),
            "entity_col_tables":     list(entity_col_tables),
            "candidate_col_summary": candidate_col_summary,
        }

        tables_found  = [t["fqn"] for t in tables_trimmed if t.get("fqn")]
        columns_found = len(columns_trimmed)

        if not tables_found:
            logger.warning("context_fetcher | NO tables in context | thread={}", state["thread_id"])

        # ── QueryPattern + AntiPattern lookup (early — before specialists run) ──
        # Patterns fetched here are stored in semantic_context so ALL specialist nodes
        # can read them without a separate Neo4j round-trip.
        _MAX_EXECUTION_COST = 2  # repair_count + recompile_count threshold for quality filter
        _question = state.get("effective_question") or state["question"]
        try:
            _all_patterns = await asyncio.to_thread(
                retry_sync,
                lambda: neo4j_client.search_query_patterns_hybrid(embedding, _question, threshold=0.72),
                service="neo4j",
            )
            _quality_patterns = [
                p for p in _all_patterns
                if (p.get("repair_count", 0) + p.get("recompile_count", 0)) <= _MAX_EXECUTION_COST
                and p.get("promotion_status") != "demoted"
            ]
            _patterns = _quality_patterns if _quality_patterns else _all_patterns
            _top = _patterns[0] if _patterns else None
            if _top:
                _last_seen_days = _top.get("last_seen_days")
                if _last_seen_days is not None and _last_seen_days > 180:
                    logger.info(
                        "context_fetcher | pattern_stale_skip | id={} | days={}",
                        (_top.get("id") or "")[:8], _last_seen_days,
                    )
                    _top = None
            if _top:
                _raw = _top.get("raw_score", 0)
                _tier = "exact" if _raw >= 0.95 else "strong" if _raw >= 0.85 else "hint"
                # Stale guard: downgrade if any table the pattern used is absent from current schema
                _known_fqns = {t["fqn"] for t in tables_trimmed if t.get("fqn")}
                if any(fqn not in _known_fqns for fqn in (_top.get("tables_used") or [])):
                    _tier = "hint"
                # Occurrence guard: a pattern seen only once hasn't been validated by repetition.
                # Cap at hint until occurrence_count >= 2 OR user has explicitly liked it.
                # Prevents a single wrong-but-SQL-valid run from becoming a strong guide.
                _occurrence = _top.get("occurrence_count", 1)
                _liked = _top.get("liked_count", 0) or 0
                if _occurrence < 2 and not _liked:
                    _tier = "hint"
                elif _occurrence < 4 and not _liked and _tier == "exact":
                    _tier = "strong"  # exact requires 4+ occurrences or an explicit like
                if (_last_seen_days or 0) > 90 and _tier != "hint":
                    _tier = "hint"
                    logger.info(
                        "context_fetcher | pattern_age_cap | id={} | days={}",
                        (_top.get("id") or "")[:8], _last_seen_days,
                    )
                _cross_d = _top.get("cross_thread_dislikes") or 0
                _cross_l = _top.get("cross_thread_likes") or 0
                if _cross_d >= 3 and _cross_d > _cross_l:
                    _tier = "hint"
                    logger.info(
                        "context_fetcher | pattern_cross_demote | id={} | cross_dislikes={}",
                        (_top.get("id") or "")[:8], _cross_d,
                    )
                elif _cross_l >= 3 and _tier == "hint":
                    _tier = "strong"
                    logger.info(
                        "context_fetcher | pattern_cross_promote | id={} | cross_likes={}",
                        (_top.get("id") or "")[:8], _cross_l,
                    )
                semantic_context["_matched_pattern"]      = _top
                semantic_context["_matched_pattern_tier"] = _tier
                # Optional corroborating 2nd pattern (same tables, strong tier only)
                _second = None
                if _tier == "strong" and len(_patterns) > 1:
                    _p2 = _patterns[1]
                    if (_p2.get("raw_score", 0) >= 0.85
                            and set(_p2.get("tables_used") or []) == set(_top.get("tables_used") or [])):
                        _second = _p2
                semantic_context["_matched_pattern_second"] = _second
                logger.info(
                    "context_fetcher | pattern_matched | tier={} | raw={:.3f} | repair={} | recompile={} | q={}",
                    _tier, _raw,
                    _top.get("repair_count", 0), _top.get("recompile_count", 0),
                    (_top.get("question_text") or "")[:60],
                )
            else:
                semantic_context["_matched_pattern"]        = None
                semantic_context["_matched_pattern_tier"]   = None
                semantic_context["_matched_pattern_second"] = None
        except Exception as _pe:
            logger.warning("context_fetcher | pattern_lookup failed | error={}", _pe)
            semantic_context["_matched_pattern"]        = None
            semantic_context["_matched_pattern_tier"]   = None
            semantic_context["_matched_pattern_second"] = None

        try:
            _anti_patterns = await asyncio.to_thread(
                retry_sync,
                lambda: neo4j_client.search_anti_patterns_hybrid(embedding, _question),
                service="neo4j",
            )
            semantic_context["_matched_anti_patterns"] = (_anti_patterns or [])[:2]
        except Exception as _ape:
            logger.warning("context_fetcher | anti_pattern_lookup failed | error={}", _ape)
            semantic_context["_matched_anti_patterns"] = []

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | tables={} | cols={} | "
            "is_cross_domain={} | hub={} | entity_pinned={} | entity_col_tables={}",
            state["thread_id"], is_followup, tables_found, columns_found,
            is_cross_domain, hub_info.get("hub_table_fqn") if hub_info else "none",
            sorted(entity_pinned_fqns), sorted(entity_col_tables),
        )

        # ── Build initial neo4j_raw_graph from all discovered nodes ──────────
        _raw_nodes: list[dict] = []
        _raw_edges: list[dict] = []
        _domain_fqn_set = set(domain_table_fqns or [])
        _intent_fqn_set = set(intent_table_fqns or [])

        for _t in tables:
            _fqn = _t.get("fqn")
            if not _fqn:
                continue
            _raw_nodes.append({"_label": "Table", **_t})
            if _t.get("community_id"):
                _raw_nodes.append({"_label": "Community", "id": _t["community_id"], "dominant_domain": _t.get("business_domain", "")})
                _raw_edges.append({"_type": "CONTAINS_TABLE", "community_id": _t["community_id"], "table_fqn": _fqn})
            if _t.get("business_domain"):
                _raw_nodes.append({"_label": "Domain", "name": _t["business_domain"]})
                _raw_edges.append({"_type": "BELONGS_TO", "table_fqn": _fqn, "domain_name": _t["business_domain"]})
            if _fqn in _intent_fqn_set:
                _raw_edges.append({"_type": "RELEVANT_TO", "table_fqn": _fqn, "intent_name": "", "source": "context_fetcher"})

        for _bt in (business_terms or []):
            if _bt.get("term"):
                _raw_nodes.append({"_label": "BusinessTerm", **_bt})

        for _i in (intents or []):
            if _i.get("name"):
                _raw_nodes.append({"_label": "Intent", **_i})

        for _qt in (templates_merged or []):
            if _qt.get("id"):
                _raw_nodes.append({"_label": "QueryTemplate", **_qt})

        neo4j_raw_graph = merge_neo4j_raw_graph({}, _raw_nodes, _raw_edges)

        return {
            "semantic_context": semantic_context,
            "effective_question": effective_question,
            "error": None,
            "neo4j_raw_graph": neo4j_raw_graph,
            "context_fetch_label": _build_context_label(semantic_context),
        }

    except Exception as e:
        logger.error("context_fetcher FAILED | thread={} | error={}", state["thread_id"], e)
        return {"error": "semantic_layer_unavailable", "semantic_context": None}


async def _fetch_group_a(
    embedding: list[float],
    search_query: str,
) -> tuple:
    """Group A: all calls independent of table discovery results.

    Runs concurrently with Group B.  All Neo4j calls are wrapped in
    asyncio.to_thread() so they don't block the event loop.
    """
    async def _templates() -> tuple:
        v   = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_query_templates(embedding), service="neo4j"
        )
        fts = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_query_templates_fulltext(search_query), service="neo4j"
        )
        return table_discovery.merge_template_results(v, fts)

    async def _business_terms() -> list:
        v   = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_business_terms_vector(embedding), service="neo4j"
        )
        fts = await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_business_terms_fulltext(search_query), service="neo4j"
        )
        return table_discovery.merge_business_terms(v, fts)

    async def _intents() -> list:
        return await asyncio.to_thread(
            retry_sync, lambda: neo4j_client.search_intents(embedding), service="neo4j"
        )

    templates_merged, business_terms, intents = await asyncio.gather(
        _templates(),
        _business_terms(),
        _intents(),
    )

    logger.info(
        "context_fetcher | group_a done | business_terms={} | intents={}",
        [b.get("term") for b in business_terms],
        [i.get("name") for i in intents],
    )
    return templates_merged, business_terms, intents


async def _fetch_group_b(
    tables: list[dict],
    embedding: list[float],
    search_query: str,
    entity_tokens: list[str] | None = None,
) -> tuple:
    """Group B: table enrichment that depends on table discovery results.

    Column loading has been removed from this phase — it is deferred to schema_enricher
    which runs AFTER anchor_resolver identifies the specific anchor tables. This eliminates
    the GLOBAL_CAP truncation problem where anchor table columns (e.g. lpp.borrowing.repayment_date)
    were cut by ranking against 14 unrelated tables.

    This group now handles:
    - Cross-domain detection (which hub table bridges domains)
    - Join-critical column identification (fast, needed for display ordering)
    - Entity hint extraction (direct value matches from entity_value discovery path)

    Column loading (display_columns + _column_lookup) runs in schema_enricher after
    anchor_resolver identifies the 2-4 relevant anchor tables.
    """
    # Cross-domain detection (sync, 1-3 Neo4j queries depending on outcome)
    tables, hub_info, is_cross_domain = await asyncio.to_thread(cross_domain.detect, tables)

    # Prioritize hub at front of list — ensures it's within anchor_resolver's table window
    if hub_info and hub_info.get("hub_table_fqn"):
        hub_fqn = hub_info["hub_table_fqn"]
        hub_entry = next((t for t in tables if t.get("fqn") == hub_fqn), None)
        if hub_entry:
            tables = [hub_entry] + [t for t in tables if t.get("fqn") != hub_fqn]
            logger.info("context_fetcher | hub_prioritized | fqn={}", hub_fqn)

    # Join-critical columns — needed for anchor_resolver context and later schema_enricher ordering
    join_crit_cols = await asyncio.to_thread(column_loader.get_join_critical_cols, tables)

    # Collect entity hints from entity_value-discovered tables.
    # UUID columns are excluded — they are internal row identifiers, never filter targets.
    entity_hints = [
        {
            "table_fqn": t["fqn"],
            "column": t["entity_matched_column"],
            "matched_value": t["entity_matched_value"],
            "token": t["entity_matched_token"],
            "match_score": t.get("entity_match_score", 0),
        }
        for t in tables
        if t.get("entity_matched_column") and t.get("fqn")
        and not column_loader._is_uuid_col(t["entity_matched_column"])
        and (t.get("entity_match_score") or 0) >= 2   # require actual value match, not description hit
    ]

    # Load a minimal fallback column set for the legacy intent_resolver path.
    # When anchor_resolver SUCCEEDS → schema_enricher loads complete columns for anchor tables.
    # When anchor_resolver FAILS → intent_resolver uses this fallback context.
    # We load only T1 (join-critical) + top semantic matches to keep it fast and non-redundant.
    # This is intentionally lighter than the old full column load — just enough for intent_resolver
    # to identify measures, filters, and join keys without hallucinating.
    display_columns, col_lookup, entity_col_tables = await asyncio.to_thread(
        column_loader.load_and_prioritize, tables, embedding, search_query, join_crit_cols, entity_tokens
    )

    return tables, hub_info, is_cross_domain, join_crit_cols, display_columns, col_lookup, entity_hints, entity_col_tables

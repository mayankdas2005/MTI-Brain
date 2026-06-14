"""Node 1b: anchor_resolver — single job: identify anchor tables + result_shape.

Runs after context_fetcher (Phase 1) with table metadata only (no columns).
Haiku model — fast classification task, not reasoning.

Output feeds schema_enricher which loads COMPLETE columns for the identified tables.
This two-pass design eliminates the GLOBAL_CAP truncation problem where anchor table
columns were cut by ranking against unrelated tables.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import _build_entity_tokens_section, build_mission_context, merge_neo4j_raw_graph
from app.services.agents.prompts import ANCHOR_RESOLVER_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_tables_section(semantic_context: dict) -> str:
    tables = (semantic_context.get("tables") or [])[:20]
    if not tables:
        return "(no tables discovered)"

    bt_fqns = {
        fqn
        for term in (semantic_context.get("business_terms") or [])
        for fqn in (term.get("related_table_fqns") or [])
    }
    intent_fqns = set(semantic_context.get("intent_table_fqns") or [])
    domain_fqns = set(semantic_context.get("domain_table_fqns") or [])

    lines = []
    for t in tables:
        fqn = t.get("fqn", "")
        desc = (t.get("description") or "")[:120]
        grain = t.get("grain", "")
        role = t.get("typical_join_role") or t.get("table_type", "")
        domain = t.get("business_domain", "")
        line = f"  {fqn}"
        if role:
            line += f"  [{role}]"
        if domain:
            line += f"  domain={domain}"
        if fqn in bt_fqns:
            line += "  [business-term — known alias for a user-mentioned concept — MUST select]"
        elif fqn in intent_fqns:
            line += "  [~ intent-related — linked to matched query intent — consider selecting]"
        elif fqn in domain_fqns:
            line += "  [~ domain-related — in matched business domain — select only if directly needed]"
        if desc:
            line += f"  — {desc}"
        if grain:
            line += f"\n    grain: {grain}"
        lines.append(line)
    return "\n".join(lines)


def _build_terms_section(semantic_context: dict) -> str:
    terms = (semantic_context.get("business_terms") or [])[:5]
    if not terms:
        return "(none)"
    lines = []
    for t in terms:
        term = t.get("term", "")
        defn = (t.get("definition") or t.get("description") or "")[:100]
        related = t.get("related_table_fqns") or []
        line = f"  {term}: {defn}"
        if related:
            line += f"  [tables: {', '.join(related)}]"
        lines.append(line)
    return "\n".join(lines)


def _build_entity_hints_section(semantic_context: dict) -> str:
    hints = (semantic_context.get("entity_hints") or [])
    if not hints:
        return "(none)"
    lines = ["Entity values the user named — these tables MUST be in anchor_tables:"]
    for h in hints:
        lines.append(f"  table={h.get('table_fqn')}  column={h.get('column')}  matched_value={h.get('token')}")
    return "\n".join(lines)


_COMPLEXITY_CAPS = {
    "simple":   {"llm": 4, "bt": 3, "intent": 2, "domain": 1},
    "complex":  {"llm": 7, "bt": 4, "intent": 3, "domain": 2},
    "advanced": {"llm": 8, "bt": 5, "intent": 4, "domain": 2},
}


def _inject_signal_tables(
    valid_anchors: list,
    semantic_context: dict,
    valid_tables: set,
    complexity: str = "simple",
    anchor_join_paths: list | None = None,
    domain_cap_override: int | None = None,
) -> list:
    """Deterministically add high-confidence signal tables after LLM selection.

    Signal priority (highest to lowest confidence):
      Signal 2: BusinessTerm.related_table_fqns — concept explicitly mapped to table (Neo4j ground truth)
      Signal 3: intent_table_fqns — table RELEVANT_TO a matched intent (Neo4j ground truth)
      Signal 4: domain_table_fqns — table BELONGS_TO a matched domain (Neo4j ground truth)
      Signal 5: Community BRIDGES_TO — cross-schema hub tables (mandatory, not capped)
      Signal 6: entity-FTS-pinned — per-entity FTS during context_fetcher

    JOINS_TO connectivity filter: only inject if table has FK edge to an LLM-selected anchor.
    For 'advanced' complexity, also allow 2-hop connections.
    JoinPath path_tables (already loaded by schema_enricher) are also treated as joinable.

    Signals 2/3/4 do NOT require the table to be in the post-14-cap valid_tables pool:
    BT/intent/domain FQNs come from Neo4j structural edges — ground truth. schema_enricher
    loads anchor columns fresh regardless of pool membership.
    """
    from app.services.agents import neo4j_client

    caps = _COMPLEXITY_CAPS.get(complexity, _COMPLEXITY_CAPS["simple"])
    bt_cap     = caps["bt"]
    intent_cap = caps["intent"]
    domain_cap = domain_cap_override if domain_cap_override is not None else caps["domain"]

    # Build joinable set: tables with JOINS_TO edge to any LLM-selected anchor
    _raw_join_edges: list[dict] = []
    try:
        direct_joins = neo4j_client.get_direct_joins(list(valid_anchors))
        joinable_fqns: set = {r["to_fqn"] for r in direct_joins} | {r["from_fqn"] for r in direct_joins}
        _raw_join_edges.extend({"_type": "JOINS_TO", "source": "anchor_resolver", **r} for r in direct_joins)
        if complexity == "advanced":
            second_hop = neo4j_client.get_direct_joins(list(joinable_fqns))
            joinable_fqns |= {r["to_fqn"] for r in second_hop} | {r["from_fqn"] for r in second_hop}
            _raw_join_edges.extend({"_type": "JOINS_TO", "source": "anchor_resolver_2hop", **r} for r in second_hop)
        # Also treat any table that appears in loaded JoinPath nodes as joinable —
        # avoids a second Neo4j call and covers multi-hop paths without JOINS_TO edges.
        for path in (anchor_join_paths or []):
            for tbl in (path.get("path_tables") or []):
                joinable_fqns.add(tbl)
        logger.info("anchor_resolver | joinable_tables | count={} | fqns={}", len(joinable_fqns), sorted(joinable_fqns))
    except Exception as e:
        logger.warning("anchor_resolver | join_connectivity_fetch failed | {} — skipping filter", e)
        joinable_fqns = valid_tables  # degrade gracefully: no connectivity filter

    def _joinable(fqn: str) -> bool:
        return fqn in joinable_fqns

    to_add: list = []
    bt_added = 0
    intent_added = 0
    domain_added = 0

    # Signal 2: BusinessTerm.related_table_fqns — Neo4j REFERENCES_TABLE edge, not LLM output
    # No valid_tables gate: BT FQNs are ground truth; injecting outside the 14-cap is safe.
    for term in (semantic_context.get("business_terms") or []):
        if bt_added >= bt_cap:
            logger.info("anchor_resolver | bt_cap_reached | cap={}", bt_cap)
            break
        for fqn in (term.get("related_table_fqns") or []):
            if bt_added >= bt_cap:
                break
            if fqn and fqn not in valid_anchors and fqn not in to_add:
                if _joinable(fqn):
                    to_add.append(fqn)
                    bt_added += 1
                    logger.info("anchor_resolver | bt_injected | {} from '{}'", fqn, term.get("term"))
                else:
                    logger.info("anchor_resolver | signal_rejected_not_joinable | fqn={} | signal=bt", fqn)

    # Signal 3: intent_table_fqns — Neo4j RELEVANT_TO edge, not LLM output
    # No valid_tables gate: same reasoning as Signal 2.
    for fqn in (semantic_context.get("intent_table_fqns") or []):
        if intent_added >= intent_cap:
            break
        if fqn and fqn not in valid_anchors and fqn not in to_add:
            if _joinable(fqn):
                to_add.append(fqn)
                intent_added += 1
                logger.info("anchor_resolver | intent_injected | {}", fqn)
            else:
                logger.info("anchor_resolver | signal_rejected_not_joinable | fqn={} | signal=intent", fqn)

    # Signal 4: domain_table_fqns — Neo4j BELONGS_TO edge, not LLM output
    # No LLM fallback for domain tables — if not pinned or in top-20, they are invisible.
    for fqn in (semantic_context.get("domain_table_fqns") or []):
        if domain_added >= domain_cap:
            break
        if fqn and fqn not in valid_anchors and fqn not in to_add:
            if _joinable(fqn):
                to_add.append(fqn)
                domain_added += 1
                logger.info("anchor_resolver | domain_injected | {}", fqn)
            else:
                logger.info("anchor_resolver | signal_rejected_not_joinable | fqn={} | signal=domain", fqn)

    result = valid_anchors + to_add

    _raw_bridge_edges: list[dict] = []
    _raw_community_nodes: list[dict] = []

    # Signal 5: Community BRIDGES_TO — cross-schema hub tables (no cap, mandatory)
    # Only runs when anchor tables span more than one community.
    try:
        table_meta = {t["fqn"]: t for t in (semantic_context.get("tables") or []) if t.get("fqn")}
        community_map = {fqn: table_meta[fqn].get("community_id") for fqn in result if fqn in table_meta}
        distinct_communities = {cid for cid in community_map.values() if cid is not None}
        if len(distinct_communities) > 1:
            bridges = neo4j_client.get_community_bridges(list(distinct_communities))
            for b in bridges:
                rel = b.get("rel") or {}
                hub_fqn = rel.get("hub_table_fqn")
                if hub_fqn and hub_fqn not in result and rel.get("join_safe", True):
                    result.append(hub_fqn)
                    logger.info("anchor_resolver | community_bridge_injected | {} between communities {}", hub_fqn, distinct_communities)
                _raw_bridge_edges.append({
                    "_type": "BRIDGES_TO",
                    "from_community_id": b.get("from_id", ""),
                    "to_community_id": b.get("to_id", ""),
                    **rel,
                })
                for cid in (b.get("from_id"), b.get("to_id")):
                    if cid:
                        _raw_community_nodes.append({"_label": "Community", "id": cid})
    except Exception as e:
        logger.warning("anchor_resolver | community_bridge_fetch failed | {} — skipping", e)

    # Signal 6: entity-FTS-pinned tables — tables pinned during context_fetcher per-entity FTS
    entity_pinned = semantic_context.get("entity_pinned_fqns") or set()
    ent_cap = 2
    ent_added = 0
    result_set = set(result)
    for fqn in sorted(entity_pinned):
        if ent_added >= ent_cap:
            break
        if fqn in result_set:
            logger.info("anchor_resolver | signal_skipped_already_selected | fqn={} | signal=entity", fqn)
            continue
        if not _joinable(fqn):
            logger.info("anchor_resolver | signal_rejected_not_joinable | fqn={} | signal=entity", fqn)
            continue
        result.append(fqn)
        result_set.add(fqn)
        ent_added += 1
        logger.info("anchor_resolver | entity_signal_injected | fqn={}", fqn)

    if len(result) > len(valid_anchors):
        logger.info(
            "anchor_resolver | signal_injection | bt={} intent={} domain={} entity={} | total={}",
            bt_added, intent_added, domain_added, ent_added, result,
        )
    return result, _raw_join_edges, _raw_bridge_edges, _raw_community_nodes


def _build_intents_section(semantic_context: dict) -> str:
    intents = (semantic_context.get("intents") or [])[:3]
    if not intents:
        return "(none)"
    return "\n".join(
        f"  {i.get('name', '')}: {(i.get('description') or '')[:100]}"
        for i in intents
    )


async def anchor_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    import asyncio
    import re

    entity_tokens = state.get("entity_tokens") or []
    logger.info("anchor_resolver START | thread={} | question={} | entity_tokens={}", state["thread_id"], state["question"][:80], entity_tokens)

    semantic_context = state.get("semantic_context") or {}
    valid_tables = {t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")}

    question = state.get("effective_question") or state["question"]
    if state.get("is_refinement") and state.get("prior_sql_tables"):
        tables_in_context = [t for t in state["prior_sql_tables"] if t in valid_tables]
        if tables_in_context:
            question = (
                f"{question}\n\n"
                f"[Refinement context: the prior query used these tables: {', '.join(tables_in_context)}. "
                f"Include them as anchor tables unless the user instruction explicitly asks to change them.]"
            )

    _query_intent_lines = state.get("query_intent") or []
    query_intent_section = (
        "CONFIRMED INTENT FROM INTAKE ANALYSIS:\n"
        + "\n".join(f"  {l}" for l in _query_intent_lines)
        if _query_intent_lines else ""
    )

    anchor_prompt = ANCHOR_RESOLVER_PROMPT.format_messages(
        question=question,
        tables_section=_build_tables_section(semantic_context),
        business_terms_section=_build_terms_section(semantic_context),
        entity_hints_section=_build_entity_hints_section(semantic_context),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        intents_section=_build_intents_section(semantic_context),
        query_intent_section=query_intent_section,
    )
    _mission = build_mission_context(
        state,
        role="Select the anchor tables from Neo4j candidates that are semantically central to this query",
        feeds="schema_enricher (column loading for selected tables), all 3 specialists (entity context)",
    )
    anchor_prompt[0].content = _mission + "\n\n" + anchor_prompt[0].content

    from app.services.agents.prompts import QUERY_PLANNER_PROMPT
    available_tables_lines = [
        f"  {t.get('fqn')} — {(t.get('business_context') or t.get('description') or '')[:80]}"
        for t in (semantic_context.get("tables") or [])[:20]
        if t.get("fqn")
    ]
    available_tables_section = (
        "AVAILABLE TABLES (verify groupings/entities against these):\n" + "\n".join(available_tables_lines)
        if available_tables_lines else ""
    )
    plan_prompt = QUERY_PLANNER_PROMPT.format_messages(
        question=question,
        available_tables_section=available_tables_section,
        entity_tokens_section=_build_entity_tokens_section(entity_tokens),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from app.core.retry import retry_async

    llm = get_llm("fast")

    @llm_breaker
    async def _call_anchor():
        return await retry_async(lambda: llm.ainvoke(anchor_prompt, config=config), service="bedrock-anchor-resolver", max_attempts=2, backoff_base=5.0)

    @llm_breaker
    async def _call_plan():
        return await retry_async(lambda: llm.ainvoke(plan_prompt, config=config), service="bedrock-query-planner", max_attempts=2, backoff_base=5.0)

    # Run both Haiku calls concurrently — wall-clock ≈ one call
    try:
        anchor_resp, plan_resp = await asyncio.gather(_call_anchor(), _call_plan(), return_exceptions=True)
    except Exception as e:
        logger.error("anchor_resolver | gather failed | thread={} | error={}", state["thread_id"], e)
        return {"anchor_tables_resolved": [], "error": f"anchor_resolver failed: {e}"}

    # Parse anchor response
    if isinstance(anchor_resp, Exception):
        logger.error("anchor_resolver | LLM failed | thread={} | error={}", state["thread_id"], anchor_resp)
        return {"anchor_tables_resolved": [], "error": f"anchor_resolver failed: {anchor_resp}"}
    raw = anchor_resp.content if isinstance(anchor_resp.content, str) else ""
    m = re.search(r"<output>(.*?)</output>", raw, re.DOTALL | re.IGNORECASE)
    json_str = m.group(1).strip() if m else raw
    try:
        import json_repair
        parsed = json_repair.loads(json_str)
    except Exception:
        logger.warning("anchor_resolver | JSON parse failed | thread={} | raw={}", state["thread_id"], raw[:200])
        return {"anchor_tables_resolved": [], "error": "anchor_resolver JSON parse failed"}

    anchor_tables = parsed.get("anchor_tables") or []
    result_shape = parsed.get("result_shape", "table")
    intent_summary = parsed.get("intent_summary", "")

    # Parse query plan response (non-fatal on failure)
    query_plan: dict = {}
    if isinstance(plan_resp, Exception):
        logger.warning("anchor_resolver | query_plan LLM failed (non-fatal) | thread={} | error={}", state["thread_id"], plan_resp)
    else:
        plan_raw = plan_resp.content if isinstance(plan_resp.content, str) else ""
        pm = re.search(r"<output>(.*?)</output>", plan_raw, re.DOTALL | re.IGNORECASE)
        plan_str = pm.group(1).strip() if pm else plan_raw
        try:
            query_plan = json_repair.loads(plan_str) or {}
        except Exception:
            logger.warning("anchor_resolver | query_plan JSON parse failed (non-fatal) | thread={}", state["thread_id"])

    logger.info(
        "anchor_resolver | query_plan | complexity={} | output_cols={} | groupings={} | time_period={} | entities={}",
        query_plan.get("complexity"), query_plan.get("expected_output_cols"),
        query_plan.get("required_groupings"), query_plan.get("required_time_period"),
        query_plan.get("explicit_entities"),
    )

    # Validate against known tables — drop hallucinated names
    valid_anchors = [t for t in anchor_tables if t in valid_tables]
    invalid = [t for t in anchor_tables if t not in valid_tables]
    if invalid:
        logger.warning("anchor_resolver | invalid_tables_dropped | {} | thread={}", invalid, state["thread_id"])

    # Complexity: intake_classifier is authoritative (saw full question context);
    # query_plan complexity is fallback when intake_classifier value is absent.
    complexity = state.get("complexity") or query_plan.get("complexity") or "simple"
    if complexity not in _COMPLEXITY_CAPS:
        complexity = "simple"
    llm_cap = _COMPLEXITY_CAPS[complexity]["llm"]

    # Hard cap on LLM selection — complexity-tiered: simple=4, complex=7, advanced=8
    valid_anchors = valid_anchors[:llm_cap]

    logger.info("anchor_resolver | llm_selected | tables={} | complexity={}", valid_anchors, complexity)

    # K2: for multi-domain queries (≥3 DOMAIN lines), expand domain_cap to cover all named domains
    _domain_lines = [l for l in (state.get("query_intent") or []) if l.startswith("DOMAIN:")]
    _domain_cap_override = min(len(_domain_lines), 6) if len(_domain_lines) >= 3 else None
    if _domain_cap_override:
        logger.info(
            "anchor_resolver | multi_domain_cap | domain_lines={} | domain_cap={}",
            len(_domain_lines), _domain_cap_override,
        )

    # Deterministic injection with JOINS_TO connectivity filter + per-signal-type caps
    valid_anchors, _raw_join_edges, _raw_bridge_edges, _raw_community_nodes = _inject_signal_tables(
        valid_anchors, semantic_context, valid_tables, complexity,
        anchor_join_paths=state.get("anchor_join_paths") or [],
        domain_cap_override=_domain_cap_override,
    )

    logger.info(
        "anchor_resolver DONE | thread={} | complexity={} | llm_cap={} | anchor_tables={} | result_shape={} | intent={}",
        state["thread_id"], complexity, llm_cap, valid_anchors, result_shape, intent_summary[:60],
    )

    # Accumulate STRUCTURALLY_SIMILAR edges for anchor tables (new query, read-only)
    _raw_struct_edges: list[dict] = []
    if valid_anchors:
        try:
            from app.services.agents import neo4j_client as _nc
            _struct_rows = await asyncio.to_thread(
                _nc.get_structurally_similar_tables, valid_anchors
            )
            _raw_struct_edges = [{"_type": "STRUCTURALLY_SIMILAR", **r} for r in (_struct_rows or [])]
        except Exception as _se:
            logger.debug("anchor_resolver | structurally_similar fetch skipped | error={}", _se)

    # Merge all raw graph data from this node into state
    _existing_raw = state.get("neo4j_raw_graph") or {}
    neo4j_raw_graph = merge_neo4j_raw_graph(
        _existing_raw,
        _raw_community_nodes,
        _raw_join_edges + _raw_bridge_edges + _raw_struct_edges,
    )

    # Store in resolved_intent stub so query_compiler can read result_shape
    existing_resolved = state.get("resolved_intent") or {}
    return {
        "anchor_tables_resolved": valid_anchors,
        "query_plan": query_plan,
        "resolved_intent": {**existing_resolved, "result_shape": result_shape, "anchor_tables": valid_anchors},
        "neo4j_raw_graph": neo4j_raw_graph,
    }

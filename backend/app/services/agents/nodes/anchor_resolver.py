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
            line += "  [⚑ business-term — known alias for a user-mentioned concept — MUST select]"
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


def _inject_signal_tables(valid_anchors: list, semantic_context: dict, valid_tables: set) -> list:
    """Deterministically add high-confidence signal tables to anchor_tables after LLM selection.

    Signal priority (highest → lowest confidence):
      Signal 2: BusinessTerm.related_table_fqns — concept explicitly mapped to table in Neo4j
      Signal 3: intent_table_fqns — table RELEVANT_TO a matched intent (2-4 specific tables)
      Signal 4: path consensus — table appeared in 4+ independent discovery paths

    Signal 1 (entity_value entity hints) has been removed. The entity_value path matches user
    tokens against all DB enum values — generic words ("cash", "days") score identically to
    true named entities ("JPMorgan"). A true named entity appears in entity_value + direct_vector
    + FTS + businessterm (4+ paths) and is handled by Signal 4 (consensus). A false positive
    only appears in entity_value (1 path) and is correctly excluded.

    Domain tables remain advisory-only — domain matches 20+ tables, too broad for force-inject.
    """
    to_add = []

    # Signal 2: BusinessTerm.related_table_fqns — named concept maps to this table (unchanged)
    for term in (semantic_context.get("business_terms") or []):
        for fqn in (term.get("related_table_fqns") or []):
            if fqn and fqn in valid_tables and fqn not in valid_anchors and fqn not in to_add:
                to_add.append(fqn)
                logger.info("anchor_resolver | business_term_injected | {} from '{}'", fqn, term.get("term"))

    # Signal 3: intent_table_fqns — tables RELEVANT_TO a matched intent.
    # Promoted from advisory to force-inject. An intent maps to 2-4 specific tables (not entire
    # domains). Guard: table must be in valid_tables (appeared in at least one discovery path).
    for fqn in (semantic_context.get("intent_table_fqns") or []):
        if fqn and fqn in valid_tables and fqn not in valid_anchors and fqn not in to_add:
            to_add.append(fqn)
            logger.info("anchor_resolver | intent_injected | {}", fqn)

    # Signal 4: path consensus — table found by 4+ independent discovery paths.
    # Fallback when Signals 2+3 don't fire (e.g. Neo4j data gaps). Multi-signal agreement
    # across direct_vector, FTS, businessterm, JoinPath, column_search etc. is strong evidence
    # of relevance to this specific query. Tables with only 1-2 paths (e.g. false positives
    # from entity_value alone) are excluded.
    for fqn in (semantic_context.get("consensus_table_fqns") or []):
        if fqn and fqn in valid_tables and fqn not in valid_anchors and fqn not in to_add:
            to_add.append(fqn)
            logger.info("anchor_resolver | consensus_injected | {} (path_count>=4)", fqn)

    if to_add:
        logger.info("anchor_resolver | signal_injection_total | added={}", to_add)
    return valid_anchors + to_add


def _build_intents_section(semantic_context: dict) -> str:
    intents = (semantic_context.get("intents") or [])[:3]
    if not intents:
        return "(none)"
    return "\n".join(
        f"  {i.get('name', '')}: {(i.get('description') or '')[:100]}"
        for i in intents
    )


async def anchor_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("anchor_resolver START | thread={} | question={}", state["thread_id"], state["question"][:80])

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

    prompt = ANCHOR_RESOLVER_PROMPT.format_messages(
        question=question,
        tables_section=_build_tables_section(semantic_context),
        business_terms_section=_build_terms_section(semantic_context),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        intents_section=_build_intents_section(semantic_context),
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-anchor-resolver", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.error("anchor_resolver | LLM failed | thread={} | error={}", state["thread_id"], e)
        return {"anchor_tables_resolved": [], "error": f"anchor_resolver failed: {e}"}

    # Extract JSON from <output> tags
    import re
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

    # Validate against known tables — drop hallucinated names
    valid_anchors = [t for t in anchor_tables if t in valid_tables]
    invalid = [t for t in anchor_tables if t not in valid_tables]
    if invalid:
        logger.warning("anchor_resolver | invalid_tables_dropped | {} | thread={}", invalid, state["thread_id"])

    # Hard cap at 4 on LLM selection — entity/term/intent/domain signals inject on top of this.
    valid_anchors = valid_anchors[:4]

    # Deterministic injection: entity-matched, business-term, intent, and domain tables
    # must always be in anchor_tables regardless of LLM selection.
    valid_anchors = _inject_signal_tables(valid_anchors, semantic_context, valid_tables)

    logger.info(
        "anchor_resolver DONE | thread={} | anchor_tables={} | result_shape={} | intent={}",
        state["thread_id"], valid_anchors, result_shape, intent_summary[:60],
    )

    # Store in resolved_intent stub so query_compiler can read result_shape
    existing_resolved = state.get("resolved_intent") or {}
    return {
        "anchor_tables_resolved": valid_anchors,
        "resolved_intent": {**existing_resolved, "result_shape": result_shape, "anchor_tables": valid_anchors},
    }

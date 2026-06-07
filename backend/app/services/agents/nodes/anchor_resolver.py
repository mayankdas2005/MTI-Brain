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
    return "\n".join(
        f"  {t.get('term', '')}: {(t.get('definition') or t.get('description') or '')[:100]}"
        for t in terms
    )


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

    # Hard cap at 4: anchor_resolver's ONLY job is picking the most relevant tables.
    # Cross-domain hub + multi-hop bridge tables are added deterministically in schema_enricher.
    valid_anchors = valid_anchors[:4]

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

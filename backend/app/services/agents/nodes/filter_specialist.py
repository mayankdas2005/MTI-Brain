"""Node 1e-B: filter_specialist — single job: extract filters.

Parallel branch dispatched by intent_dispatcher via LangGraph Send API.
Sees filterable columns from anchor tables with FULL metadata including sample_values
(currently stripped from intent_resolver but valuable for identifying the right column).

CRITICAL: raw_user_value must ALWAYS be user's exact words, NEVER a DB code.
filter_resolver downstream handles Tiers 1-5 value resolution.
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_refinement_section
from app.services.agents.prompts import FILTER_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_filterable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    # Include all columns with medium/high filter selectivity, or date columns, or has distinct values
    filterable = [
        c for c in columns
        if c.get("filter_selectivity") in ("high", "medium")
        or c.get("semantic_type") in ("date", "code", "identifier", "dimension")
        or c.get("temporal_grain")
    ]
    if not filterable:
        filterable = columns  # fallback: show all

    lines = []
    for c in filterable[:20]:
        fqn = c.get("table_fqn", "")
        name = c.get("name", "")
        dtype = c.get("data_type") or c.get("semantic_type", "")
        desc = (c.get("description") or "")[:200]
        synonyms = c.get("synonyms") or []
        filter_vals = (c.get("filter_values") or [])[:10]
        # value_aliases shows the CODE -> Human Name mapping — useful context for filter_specialist
        # but it MUST NOT cause the specialist to output DB codes as raw_user_value
        value_aliases = (c.get("value_aliases") or [])[:5]
        # sample_values: actual data examples — shown here but stripped from current intent_resolver
        sample_vals = (c.get("sample_values") or [])[:5]
        temporal_grain = c.get("temporal_grain", "")

        lines.append(f"  {fqn}.{name}  [{dtype}]")
        if temporal_grain and temporal_grain != "none":
            lines.append(f"    temporal_grain: {temporal_grain}")
        if desc:
            lines.append(f"    description: {desc}")
        if synonyms:
            lines.append(f"    also known as: {', '.join(synonyms[:3])}")
        if filter_vals:
            lines.append(f"    filter_values (DB codes): {filter_vals}")
        if value_aliases:
            lines.append(f"    meanings (CODE -> Name): {value_aliases}")
        if sample_vals:
            lines.append(f"    sample_values: {sample_vals}")
    return "\n".join(lines)


async def filter_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("filter_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    intent_summary = resolved_intent.get("intent_summary", state.get("question", ""))

    prompt = FILTER_SPECIALIST_PROMPT.format_messages(
        question=state["question"],
        intent_summary=intent_summary,
        filterable_columns_section=_build_filterable_columns_section(enriched_schema),
        refinement_section=build_refinement_section(state, role="filters"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-filter-specialist", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.error("filter_specialist | LLM failed | thread={} | error={}", state.get("thread_id"), e)
        return {"specialist_outputs": [{"type": "filters", "error": str(e)}]}

    m = re.search(r"<output>(.*?)</output>", raw, re.DOTALL | re.IGNORECASE)
    json_str = m.group(1).strip() if m else raw

    try:
        import json_repair
        parsed = json_repair.loads(json_str)
    except Exception:
        logger.warning("filter_specialist | JSON parse failed | thread={} | raw={}", state.get("thread_id"), raw[:200])
        return {"specialist_outputs": [{"type": "filters", "filters": [], "timeframe": None, "time_filter_col": None}]}

    # temporal_grains: accept list from new prompt format OR fall back to
    # singular temporal_grain for backward compat.
    raw_grains = parsed.get("temporal_grains") or []
    if not raw_grains:
        tg = parsed.get("temporal_grain")
        raw_grains = [tg] if tg else []

    result = {
        "type": "filters",
        "filters": parsed.get("filters", []),
        "timeframe": parsed.get("timeframe"),
        "temporal_grains": raw_grains,
        "time_filter_col": parsed.get("time_filter_col"),
        "filter_directive_hint": parsed.get("filter_directive_hint", ""),
    }
    logger.info(
        "filter_specialist DONE | thread={} | filters={} | timeframe={} | time_col={}",
        state.get("thread_id"),
        [f.get("column_name") for f in result["filters"]],
        result["timeframe"],
        result["time_filter_col"],
    )
    return {"specialist_outputs": [result]}

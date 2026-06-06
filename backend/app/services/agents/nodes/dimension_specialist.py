"""Node 1e-C: dimension_specialist — single job: extract dimensions.

Parallel branch dispatched by intent_dispatcher via LangGraph Send API.
Sees is_groupable=True columns from anchor tables (complete, no truncation).
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_refinement_section
from app.services.agents.prompts import DIMENSION_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_groupable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    groupable = [c for c in columns if c.get("is_groupable")]
    if not groupable:
        return "(no groupable columns found)"

    lines = []
    for c in groupable[:20]:
        fqn = c.get("table_fqn", "")
        name = c.get("name", "")
        dtype = c.get("data_type") or c.get("semantic_type", "")
        desc = (c.get("description") or "")[:150]
        synonyms = c.get("synonyms") or []
        lines.append(f"  {fqn}.{name}  [{dtype}]")
        if desc:
            lines.append(f"    description: {desc}")
        if synonyms:
            lines.append(f"    also known as: {', '.join(synonyms[:3])}")
    return "\n".join(lines)


async def dimension_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("dimension_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    intent_summary = resolved_intent.get("intent_summary", state.get("question", ""))

    # Build compact summary of measures already selected (from specialist_outputs if available)
    measures_summary = "(measures not yet available — avoid duplicating numeric aggregation columns as dimensions)"
    specialist_outputs = state.get("specialist_outputs") or []
    for s in specialist_outputs:
        if s.get("type") == "measures" and s.get("measures"):
            measure_names = [m.get("column_name", "") for m in s["measures"]]
            measures_summary = f"Already selected as measures: {', '.join(measure_names)}"
            break

    prompt = DIMENSION_SPECIALIST_PROMPT.format_messages(
        question=state["question"],
        intent_summary=intent_summary,
        groupable_columns_section=_build_groupable_columns_section(enriched_schema),
        refinement_section=build_refinement_section(state, role="dimensions"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        measures_summary=measures_summary,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-dimension-specialist", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.error("dimension_specialist | LLM failed | thread={} | error={}", state.get("thread_id"), e)
        return {"specialist_outputs": [{"type": "dimensions", "error": str(e)}]}

    m = re.search(r"<output>(.*?)</output>", raw, re.DOTALL | re.IGNORECASE)
    json_str = m.group(1).strip() if m else raw

    try:
        import json_repair
        parsed = json_repair.loads(json_str)
    except Exception:
        logger.warning("dimension_specialist | JSON parse failed | thread={} | raw={}", state.get("thread_id"), raw[:200])
        return {"specialist_outputs": [{"type": "dimensions", "dimensions": []}]}

    result = {
        "type": "dimensions",
        "dimensions": parsed.get("dimensions", []),
        "dimension_directive": parsed.get("dimension_directive", ""),
    }
    logger.info(
        "dimension_specialist DONE | thread={} | dimensions={}",
        state.get("thread_id"), [d.get("column_name") for d in result["dimensions"]],
    )
    return {"specialist_outputs": [result]}

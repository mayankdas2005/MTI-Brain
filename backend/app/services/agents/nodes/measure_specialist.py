"""Node 1e-A: measure_specialist — single job: extract measures.

Parallel branch dispatched by intent_dispatcher via LangGraph Send API.
Sees ONLY is_measurable=True columns from anchor tables (complete, no truncation).
Outputs a fragment appended to state["specialist_outputs"] via Annotated[list, operator.add].
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_refinement_section
from app.services.agents.prompts import MEASURE_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_measurable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    measurable = [c for c in columns if c.get("is_measurable")]
    if not measurable:
        return "(no measurable columns found — check anchor table schema)"

    lines = []
    for c in measurable:
        fqn = c.get("table_fqn", "")
        name = c.get("name", "")
        dtype = c.get("data_type") or c.get("semantic_type", "")
        desc = (c.get("description") or "")[:200]
        default_agg = c.get("default_aggregation", "SUM")
        synonyms = c.get("synonyms") or []
        lines.append(f"  {fqn}.{name}  [{dtype}]  default={default_agg}")
        if desc:
            lines.append(f"    description: {desc}")
        if synonyms:
            lines.append(f"    also known as: {', '.join(synonyms[:3])}")
    return "\n".join(lines)


async def measure_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("measure_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    intent_summary = resolved_intent.get("intent_summary", state.get("question", ""))

    prompt = MEASURE_SPECIALIST_PROMPT.format_messages(
        question=state["question"],
        intent_summary=intent_summary,
        measurable_columns_section=_build_measurable_columns_section(enriched_schema),
        refinement_section=build_refinement_section(state, role="measures"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-measure-specialist", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.error("measure_specialist | LLM failed | thread={} | error={}", state.get("thread_id"), e)
        return {"specialist_outputs": [{"type": "measures", "error": str(e)}]}

    m = re.search(r"<output>(.*?)</output>", raw, re.DOTALL | re.IGNORECASE)
    json_str = m.group(1).strip() if m else raw

    try:
        import json_repair
        parsed = json_repair.loads(json_str)
    except Exception:
        logger.warning("measure_specialist | JSON parse failed | thread={} | raw={}", state.get("thread_id"), raw[:200])
        return {"specialist_outputs": [{"type": "measures", "measures": [], "measure_directive": ""}]}

    result = {
        "type": "measures",
        "measures": parsed.get("measures", []),
        "measure_directive": parsed.get("measure_directive", ""),
    }
    logger.info(
        "measure_specialist DONE | thread={} | measures={}",
        state.get("thread_id"), [m.get("column_name") for m in result["measures"]],
    )
    return {"specialist_outputs": [result]}

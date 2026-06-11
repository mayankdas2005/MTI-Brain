"""Node 1e-A: measure_specialist — single job: extract measures.

Parallel branch dispatched by intent_dispatcher via LangGraph Send API.
Sees numeric/measure columns from anchor tables (semantic_type or data_type based, no truncation).
Outputs a fragment appended to state["specialist_outputs"] via Annotated[list, operator.add].
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import (
    _build_concept_mappings_section,
    _build_entity_tokens_section,
    build_joinable_table_graph_section,
    build_refinement_section,
)
from app.services.agents.prompts import MEASURE_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_query_plan_section(query_plan: dict | None) -> str:
    if not query_plan:
        return ""
    lines = ["USER'S EXPLICIT REQUIREMENTS — your output MUST satisfy these:"]
    cols = query_plan.get("expected_output_cols") or []
    if cols:
        lines.append(f"  Metrics/columns user requested: {', '.join(cols)}")
    if query_plan.get("is_detail_request"):
        lines.append("  Query type: DETAIL/LIST — output measures: [] (no aggregation)")
    else:
        lines.append("  Query type: SUMMARY/AGGREGATE — produce measures for each requested metric")
    time_period = query_plan.get("required_time_period")
    if time_period:
        lines.append(f"  Time period: {time_period}")
    return "\n".join(lines)


_NUMERIC_DTYPES = frozenset({
    "numeric", "decimal", "integer", "bigint", "float", "double precision",
    "real", "smallint", "int", "int2", "int4", "int8", "float4", "float8",
})
_EXCLUDE_SEMANTIC = {"free_text"}


def _is_measurable(c: dict) -> bool:
    sem = c.get("semantic_type", "").lower()
    if sem in _EXCLUDE_SEMANTIC:
        return False
    return c.get("data_type", "").lower() in _NUMERIC_DTYPES


def _build_measurable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    measurable = [c for c in columns if _is_measurable(c)]
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
        sample_vals = (c.get("sample_values") or [])[:4]
        lines.append(f"  {fqn}.{name}  [{dtype}]  default={default_agg}")
        if desc:
            lines.append(f"    description: {desc}")
        if synonyms:
            lines.append(f"    also known as: {', '.join(synonyms[:3])}")
        if sample_vals:
            lines.append(f"    sample_values: {sample_vals}")
    return "\n".join(lines)


async def measure_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("measure_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    query_intent_lines = state.get("query_intent") or []
    if query_intent_lines:
        intent_summary = "USER'S STATED GOAL (framing only — do NOT derive column names from this section):\n" + "\n".join(query_intent_lines)
    else:
        intent_summary = resolved_intent.get("intent_summary", state.get("effective_question") or state.get("question", ""))

    prompt = MEASURE_SPECIALIST_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        intent_summary=intent_summary,
        measurable_columns_section=_build_measurable_columns_section(enriched_schema),
        joinable_table_graph=build_joinable_table_graph_section(state.get("anchor_join_paths")),
        refinement_section=build_refinement_section(state, role="measures"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        query_plan_section=_build_query_plan_section(state.get("query_plan")),
        concept_mappings_section=_build_concept_mappings_section(state.get("concept_mappings")),
        entity_tokens_section=_build_entity_tokens_section(state.get("entity_tokens") or []),
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

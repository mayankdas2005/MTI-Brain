"""Node 1e-C: dimension_specialist — single job: extract dimensions.

Parallel branch dispatched by intent_dispatcher via LangGraph Send API.
Sees dimension/code/flag semantic_type columns from anchor tables (complete, no truncation).
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import (
    _build_entity_tokens_section,
    build_joinable_table_graph_section,
    build_mission_context,
    build_refinement_section,
)
from app.services.agents.prompts import DIMENSION_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_query_plan_section(query_plan: dict | None) -> str:
    if not query_plan:
        return ""
    lines = ["USER'S EXPLICIT REQUIREMENTS — your output MUST satisfy these:"]
    groupings = query_plan.get("required_groupings") or []
    if groupings:
        lines.append(f"  Group/break down by: {', '.join(groupings)}")
    cols = query_plan.get("expected_output_cols") or []
    if cols:
        lines.append(f"  User requested output: {', '.join(cols)}")
    if query_plan.get("is_detail_request"):
        lines.append("  Query type: DETAIL/LIST — use natural grain (PK/reference) as dimensions")
    return "\n".join(lines)


_NUMERIC_DTYPES = frozenset({
    "numeric", "decimal", "integer", "bigint", "float", "double precision",
    "real", "smallint", "int", "int2", "int4", "int8", "float4", "float8",
})
_EXCLUDE_SEMANTIC = {"free_text"}


def _is_dimension(c: dict) -> bool:
    sem = c.get("semantic_type", "").lower()
    if sem in _EXCLUDE_SEMANTIC:
        return False
    dtype = c.get("data_type", "").lower()
    if dtype in _NUMERIC_DTYPES:
        return False
    return True


def _build_groupable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    table_grains = enriched_schema.get("table_grains") or {}
    groupable = [c for c in columns if _is_dimension(c)]
    if not groupable:
        return "(no groupable columns found)"

    by_table: dict[str, list[dict]] = {}
    for c in groupable[:20]:
        by_table.setdefault(c.get("table_fqn", ""), []).append(c)

    lines = []
    for fqn, cols in by_table.items():
        grain = table_grains.get(fqn, "")
        grain_note = f"  [grain: {grain[:100]}]" if grain else ""
        lines.append(f"{fqn}{grain_note}")
        for c in cols:
            name = c.get("name", "")
            sem = c.get("semantic_type") or c.get("data_type", "")
            desc = (c.get("description") or "")[:150]
            synonyms = c.get("synonyms") or []
            distinct_vals = c.get("distinct_values") or c.get("value_vocabulary") or []
            sample_vals = (c.get("sample_values") or [])[:5]
            lines.append(f"  [{sem}] {name}")
            if desc:
                lines.append(f"    description: {desc}")
            if synonyms:
                lines.append(f"    also known as: {', '.join(synonyms[:3])}")
            if distinct_vals:
                lines.append(f"    example_values: {distinct_vals[:8]}")
            elif sample_vals:
                lines.append(f"    example_values: {sample_vals}")
    return "\n".join(lines)


async def dimension_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("dimension_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    query_intent_lines = state.get("query_intent") or []
    if query_intent_lines:
        intent_summary = "USER'S STATED GOAL (framing only — do NOT derive column names from this section):\n" + "\n".join(query_intent_lines)
    else:
        intent_summary = resolved_intent.get("intent_summary", state.get("effective_question") or state.get("question", ""))

    # Build compact summary of measures already selected (from specialist_outputs if available)
    measures_summary = "(measures not yet available — avoid duplicating numeric aggregation columns as dimensions)"
    specialist_outputs = state.get("specialist_outputs") or []
    for s in specialist_outputs:
        if s.get("type") == "measures" and s.get("measures"):
            measure_names = [m.get("column_name", "") for m in s["measures"]]
            measures_summary = f"Already selected as measures: {', '.join(measure_names)}"
            break

    # N4: prefer LLM-cleaned explicit_entities from anchor_resolver's query_plan over raw
    # entity_tokens (which may include measure names like 'closing balance').
    _query_plan = state.get("query_plan") or {}
    _explicit = _query_plan.get("explicit_entities") or []
    _effective_entity_tokens = _explicit if _explicit else (state.get("entity_tokens") or [])

    prompt = DIMENSION_SPECIALIST_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        intent_summary=intent_summary,
        groupable_columns_section=_build_groupable_columns_section(enriched_schema),
        joinable_table_graph=build_joinable_table_graph_section(state.get("anchor_join_paths")),
        refinement_section=build_refinement_section(state, role="dimensions"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        measures_summary=measures_summary,
        query_plan_section=_build_query_plan_section(state.get("query_plan")),
        entity_tokens_section=_build_entity_tokens_section(_effective_entity_tokens),
    )
    _mission = build_mission_context(
        state,
        role="Identify grouping dimensions and temporal grain(s) for the query",
        feeds="intent_assembler → directive_writer (dimension_intent, temporal_grains)",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

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

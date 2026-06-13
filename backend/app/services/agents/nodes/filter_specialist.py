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
from app.services.agents.helpers import (
    _build_entity_tokens_section,
    build_joinable_table_graph_section,
    build_refinement_section,
)
from app.services.agents.prompts import FILTER_SPECIALIST_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


_NUMERIC_DTYPES = frozenset({
    "numeric", "decimal", "integer", "bigint", "float", "double precision",
    "real", "smallint", "int", "int2", "int4", "int8", "float4", "float8",
})
_NEVER_FILTER = {"free_text"}


def _build_filterable_columns_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    filterable = [
        c for c in columns
        if c.get("semantic_type", "") not in _NEVER_FILTER
        and (
            "date" in (c.get("data_type") or "").lower()
            or "timestamp" in (c.get("data_type") or "").lower()
            or (c.get("data_type") or "").lower() not in _NUMERIC_DTYPES
        )
    ]
    if not filterable:
        filterable = columns  # fallback: show all

    # Per-table cap: each anchor table gets at most 6 columns
    from collections import defaultdict
    by_table: dict = defaultdict(list)
    for c in filterable:
        by_table[c.get("table_fqn", "")].append(c)
    capped: list = []
    for tbl_cols in by_table.values():
        capped.extend(tbl_cols[:6])

    header_lines = [
        "FILTERABLE COLUMNS — known_values listed are DB enum codes (reference only).",
        "Your raw_user_value MUST be the user's exact words. The downstream resolver maps them to DB codes.",
        "",
    ]
    lines = header_lines[:]
    for c in capped:
        fqn = c.get("table_fqn", "")
        name = c.get("name", "")
        dtype = c.get("data_type") or c.get("semantic_type", "")
        desc = (c.get("description") or "")[:200]
        synonyms = c.get("synonyms") or []
        distinct_vals = c.get("distinct_values") or c.get("value_vocabulary") or []
        value_aliases = c.get("value_aliases") or []
        sample_vals = (c.get("sample_values") or [])[:5]
        n_distinct = c.get("n_distinct") or -1

        dtype_lower = (dtype or "").lower()
        is_date_type = "date" in dtype_lower or "timestamp" in dtype_lower
        time_label = " [time-filter eligible]" if is_date_type else ""

        lines.append(f"  {fqn}.{name}  [{dtype}]{time_label}")
        if desc:
            lines.append(f"    description: {desc}")
        if synonyms:
            lines.append(f"    also known as: {', '.join(synonyms[:3])}")
        if distinct_vals:
            is_exhaustive = len(distinct_vals) > 0 and n_distinct > 0 and len(distinct_vals) >= n_distinct
            label = "all_values (complete set)" if is_exhaustive else "known_values (may be partial)"
            lines.append(f"    {label}: {distinct_vals[:20]}")
        elif sample_vals:
            lines.append(f"    sample_values: {sample_vals}")
        if value_aliases:
            lines.append(f"    code_mappings (DB_CODE -> human name): {value_aliases[:8]}")
            lines.append(f"      Use human name as raw_user_value — downstream resolves to DB code")
    return "\n".join(lines)


def _build_query_plan_section(query_plan: dict | None) -> str:
    if not query_plan:
        return ""
    lines = ["USER'S EXPLICIT REQUIREMENTS — your output MUST satisfy these:"]
    time_period = query_plan.get("required_time_period")
    if time_period:
        lines.append(f"  Time period (MUST be extracted): {time_period}")
    entities = query_plan.get("explicit_entities") or []
    if entities:
        lines.append(f"  Named entities to filter on: {', '.join(entities)}")
    cols = query_plan.get("expected_output_cols") or []
    if cols:
        lines.append(f"  User requested output: {', '.join(cols)}")
    return "\n".join(lines)


def _build_entity_hints_section(entity_hints: list) -> str:
    if not entity_hints:
        return ""
    lines = [
        "ENTITY HINTS — named entities found in the query. You MUST include a filter for each.",
        "Use the user's exact word as raw_user_value (the downstream system resolves it to DB code):",
    ]
    for eh in entity_hints:
        lines.append(
            f'  User said "{eh.get("token")}" → filter on {eh.get("table_fqn")}.{eh.get("column")}'
            f'   (set raw_user_value = "{eh.get("token")}")'
        )
    return "\n".join(lines)


async def filter_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    _sc = state.get("semantic_context") or {}
    entity_hints = _sc.get("entity_hints") or []
    entity_tokens = state.get("entity_tokens") or []

    logger.info(
        "filter_specialist START | thread={} | entity_hints={} | entity_tokens={}",
        state.get("thread_id", ""),
        [(eh.get("token"), f"{eh.get('table_fqn')}.{eh.get('column')}") for eh in entity_hints],
        entity_tokens,
    )

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    query_intent_lines = state.get("query_intent") or []
    if query_intent_lines:
        intent_summary = "USER'S STATED GOAL (framing only — do NOT derive column names from this section):\n" + "\n".join(query_intent_lines)
    else:
        intent_summary = resolved_intent.get("intent_summary", state.get("effective_question") or state.get("question", ""))

    # N4: prefer LLM-cleaned explicit_entities from anchor_resolver's query_plan over raw
    # entity_tokens (which may include measure names like 'closing balance').
    _query_plan = state.get("query_plan") or {}
    _explicit = _query_plan.get("explicit_entities") or []
    _effective_entity_tokens = _explicit if _explicit else entity_tokens

    _cond_lines = [l for l in query_intent_lines if l.startswith("CONDITION:")]
    _condition_lines_section = "\n".join(_cond_lines) if _cond_lines else "(none)"

    # X5: temporal anchor constraint — force time_filter_col to come from the fact table
    _temporal_anchor = (_query_plan.get("temporal_anchor_fqn") or "").strip()
    temporal_anchor_section = (
        f"TIME FILTER CONSTRAINT: time_filter_col MUST be from table {_temporal_anchor}.\n"
        f"Only select time_filter_col from a different table if {_temporal_anchor} has NO [time-filter eligible] columns."
    ) if _temporal_anchor else ""

    prompt = FILTER_SPECIALIST_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        intent_summary=intent_summary,
        filterable_columns_section=_build_filterable_columns_section(enriched_schema),
        joinable_table_graph=build_joinable_table_graph_section(state.get("anchor_join_paths")),
        refinement_section=build_refinement_section(state, role="filters"),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        query_plan_section=_build_query_plan_section(state.get("query_plan")),
        entity_hints_section=_build_entity_hints_section(entity_hints),
        entity_tokens_section=_build_entity_tokens_section(_effective_entity_tokens),
        condition_lines_section=_condition_lines_section,
        temporal_anchor_section=temporal_anchor_section,
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
    filter_detail = [
        f"{f.get('column_name')} ({f.get('table_fqn')}) ← raw='{f.get('raw_user_value')}'"
        for f in result["filters"]
    ]
    logger.info(
        "filter_specialist DONE | thread={} | filters={} | timeframe={} | time_col={} | filter_detail={}",
        state.get("thread_id"),
        [f.get("column_name") for f in result["filters"]],
        result["timeframe"],
        result["time_filter_col"],
        filter_detail,
    )
    return {"specialist_outputs": [result]}

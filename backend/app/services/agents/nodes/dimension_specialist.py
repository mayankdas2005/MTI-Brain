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
    table_row_counts = enriched_schema.get("table_row_counts") or {}
    groupable = [c for c in columns if _is_dimension(c)]
    if not groupable:
        return "(no groupable columns found)"

    by_table: dict[str, list[dict]] = {}
    for c in groupable[:20]:
        by_table.setdefault(c.get("table_fqn", ""), []).append(c)

    lines = []
    for fqn, cols in by_table.items():
        grain = table_grains.get(fqn, "")
        row_count = table_row_counts.get(fqn, 0)
        grain_note = f"  [grain: {grain[:100]}]" if grain else ""
        row_note = f"  rows={row_count:,}" if row_count else ""
        lines.append(f"{fqn}{grain_note}{row_note}")
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

    # ── Prior pattern injection ─────────────────────────────────────────────────
    _sc   = state.get("semantic_context") or {}
    _pat  = _sc.get("_matched_pattern")
    _pat2 = _sc.get("_matched_pattern_second")
    _anti = _sc.get("_matched_anti_patterns") or []
    _tier = _sc.get("_matched_pattern_tier")

    prior_verified_section = ""
    prior_trace_row = ""

    if _pat and _tier in ("exact", "strong") and _pat.get("dimension_summary"):
        _corroboration = ""
        if _pat2 and _pat2.get("dimension_summary"):
            _corroboration = f"\nCORROBORATED BY 2nd pattern: {_pat2['dimension_summary']}"
        _anti_sql_lines = "\n".join(
            f"Prior SQL error: {a.get('error_type', 'error')} on {a.get('failing_element', 'unknown')}"
            for a in _anti if a.get("error_type")
        )
        verb = "EXACT MATCH" if _tier == "exact" else "STRONG MATCH"
        prior_verified_section = (
            f"<prior_pattern>\n"
            f"Similar question: \"{_pat.get('question_text', '')}\"\n"
            f"{verb} — Prior verified dimension interpretation:\n"
            f"  {_pat['dimension_summary']}{_corroboration}\n"
            + (f"Note — similar questions had SQL errors (not interpretation errors):\n{_anti_sql_lines}\n" if _anti_sql_lines else "")
            + "Keep this in mind as you fill the DBA TRACE below. "
            "Adopt if the current question aligns. If different, explain the deviation in <reasoning>.\n"
            "</prior_pattern>"
        )
        prior_trace_row = (
            f"| **[PRIOR — consider]** {_pat['dimension_summary']} "
            f"| — | — | — | {_tier} match — adopt or explain deviation |\n"
        )
    elif _anti:
        _anti_sql_lines = "\n".join(
            f"SQL error: {a.get('error_type', 'error')} on {a.get('failing_element', 'unknown')}"
            for a in _anti if a.get("error_type")
        )
        if _anti_sql_lines:
            prior_verified_section = (
                f"<prior_failed>\nSimilar questions previously had SQL errors (interpretation may still be correct):\n{_anti_sql_lines}\n</prior_failed>"
            )

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
        prior_verified_section=prior_verified_section,
        prior_trace_row=prior_trace_row,
    )
    _mission = build_mission_context(
        state,
        role="Identify grouping dimensions and temporal grain(s) for the query",
        feeds="intent_assembler → directive_writer (dimension_intent, temporal_grains)",
    )
    from app.services.agents.helpers import format_prior_context_block
    _prior_ctx_block = format_prior_context_block(state.get("prior_execution_context"))
    prompt[0].content = _mission + "\n\n" + (_prior_ctx_block + "\n" if _prior_ctx_block else "") + prompt[0].content

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
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            raise ValueError(f"unexpected type {type(parsed).__name__}")
    except Exception:
        logger.warning("dimension_specialist | JSON parse failed | thread={} | raw={}", state.get("thread_id"), raw[:200])
        return {"specialist_outputs": [{"type": "dimensions", "dimensions": []}]}

    result = {
        "type": "dimensions",
        "dimensions": parsed.get("dimensions", []),
        "dimension_directive": parsed.get("dimension_directive", ""),
    }
    dim_cols = [d.get("column_name") for d in result["dimensions"]]
    dim_summary = " | ".join(
        f"{(d.get('table_fqn') or '').split('.')[-1]}.{d.get('column_name')} → {d.get('alias', '')}"
        for d in result["dimensions"] if d.get("column_name")
    ) or (parsed.get("dimension_directive") or "none")[:200]

    logger.info(
        "dimension_specialist DONE | thread={} | dimensions={} | prior_tier={}",
        state.get("thread_id"), dim_cols,
        (_sc.get("_matched_pattern_tier") or "none"),
    )
    return {"specialist_outputs": [result], "_dimension_specialist_output": dim_summary}

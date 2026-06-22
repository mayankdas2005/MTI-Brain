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
    build_mission_context,
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
    table_grains = enriched_schema.get("table_grains") or {}
    table_row_counts = enriched_schema.get("table_row_counts") or {}
    measurable = [c for c in columns if _is_measurable(c)]
    if not measurable:
        return "(no measurable columns found — check anchor table schema)"

    by_table: dict[str, list[dict]] = {}
    for c in measurable:
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
            desc = (c.get("description") or "")[:200]
            default_agg = c.get("default_aggregation", "SUM")
            synonyms = c.get("synonyms") or []
            sample_vals = (c.get("sample_values") or [])[:4]
            lines.append(f"  [{sem}] {name}  default={default_agg}")
            if desc:
                lines.append(f"    description: {desc}")
            if synonyms:
                lines.append(f"    also known as: {', '.join(synonyms[:3])}")
            if sample_vals:
                lines.append(f"    sample_values: {sample_vals}")
    return "\n".join(lines)


def _compact_measure_summary(parsed: dict) -> str:
    parts = []
    for m in (parsed.get("measures") or []):
        col   = m.get("column_name", "")
        agg   = m.get("aggregation", "")
        alias = m.get("alias", "")
        tbl   = (m.get("table_fqn") or "").split(".")[-1]
        if col:
            parts.append(f"{agg}({tbl}.{col}) → {alias}" if alias else f"{agg}({tbl}.{col})")
    for d in (parsed.get("derived_measures") or []):
        alias = d.get("alias", "")
        expr  = (d.get("expression") or "")[:60]
        if alias:
            parts.append(f"{alias} = {expr}")
    if not parts:
        directive = (parsed.get("measure_directive") or "")
        return directive[:200]
    return " | ".join(parts[:4])


def _build_prior_sections_measure(semantic_context: dict) -> tuple[str, str]:
    pat   = semantic_context.get("_matched_pattern")
    pat2  = semantic_context.get("_matched_pattern_second")
    anti  = semantic_context.get("_matched_anti_patterns") or []
    tier  = semantic_context.get("_matched_pattern_tier")

    if pat and tier in ("exact", "strong") and pat.get("measure_summary"):
        corroboration = ""
        if pat2 and pat2.get("measure_summary"):
            corroboration = f"\nCORROBORATED BY 2nd pattern: {pat2['measure_summary']}"
        anti_sql_lines = "\n".join(
            f"Prior SQL error: {a.get('error_type', 'error')} on {a.get('failing_element', 'unknown')}"
            for a in anti
        )
        verb = "EXACT MATCH" if tier == "exact" else "STRONG MATCH"
        prior_verified_section = (
            f"<prior_pattern>\n"
            f"Similar question: \"{pat.get('question_text', '')}\"\n"
            f"{verb} — Prior verified measure interpretation:\n"
            f"  {pat['measure_summary']}{corroboration}\n"
            + (f"Note — similar questions had SQL errors (not interpretation errors):\n{anti_sql_lines}\n" if anti_sql_lines else "")
            + "Keep this in mind as you fill the DBA TRACE below. "
            "Adopt if the current question aligns. If different, explain the deviation in <reasoning>.\n"
            "</prior_pattern>"
        )
        prior_trace_row = (
            f"| **[PRIOR — consider]** {pat['measure_summary']} "
            f"| (prior verified) | — | — | — | {tier} match — adopt or explain deviation |\n"
        )
        return prior_verified_section, prior_trace_row

    anti_sql_lines = "\n".join(
        f"SQL error: {a.get('error_type', 'error')} on {a.get('failing_element', 'unknown')}"
        for a in anti if a.get("error_type")
    )
    if anti_sql_lines:
        return (
            f"<prior_failed>\nSimilar questions previously had SQL errors (interpretation may still be correct):\n{anti_sql_lines}\n</prior_failed>",
            "",
        )
    return "", ""


async def measure_specialist(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("measure_specialist START | thread={}", state.get("thread_id", ""))

    enriched_schema = state.get("enriched_schema") or {}
    resolved_intent = state.get("resolved_intent") or {}
    query_intent_lines = state.get("query_intent") or []
    if query_intent_lines:
        intent_summary = "USER'S STATED GOAL (framing only — do NOT derive column names from this section):\n" + "\n".join(query_intent_lines)
    else:
        intent_summary = resolved_intent.get("intent_summary", state.get("effective_question") or state.get("question", ""))

    _sc = state.get("semantic_context") or {}
    prior_verified_section, prior_trace_row = _build_prior_sections_measure(_sc)

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
        prior_verified_section=prior_verified_section,
        prior_trace_row=prior_trace_row,
    )
    _mission = build_mission_context(
        state,
        role="Identify all metrics and aggregation expressions the query requires from schema columns",
        feeds="intent_assembler → directive_writer (measure_intent)",
    )
    from app.services.agents.helpers import format_prior_context_block
    _prior_ctx_block = format_prior_context_block(
        state.get("prior_context_window") or state.get("prior_execution_context")
    )
    prompt[0].content = _mission + "\n\n" + (_prior_ctx_block + "\n" if _prior_ctx_block else "") + prompt[0].content

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
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            raise ValueError(f"unexpected type {type(parsed).__name__}")
    except Exception:
        logger.warning("measure_specialist | JSON parse failed | thread={} | raw={}", state.get("thread_id"), raw[:200])
        return {"specialist_outputs": [{"type": "measures", "measures": [], "measure_directive": ""}]}

    result = {
        "type": "measures",
        "measures": parsed.get("measures", []),
        "measure_directive": parsed.get("measure_directive", ""),
    }
    output_summary = _compact_measure_summary(parsed)
    logger.info(
        "measure_specialist DONE | thread={} | measures={} | prior_tier={}",
        state.get("thread_id"), [m.get("column_name") for m in result["measures"]],
        (_sc.get("_matched_pattern_tier") or "none"),
    )
    return {"specialist_outputs": [result], "_measure_specialist_output": output_summary}

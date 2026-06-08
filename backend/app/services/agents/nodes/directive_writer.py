"""Node 1g: directive_writer — single job: write the intent directive.

Runs after intent_assembler with the complete assembled intent.
Sonnet model — needs to reason about CTE patterns, schema gaps, computation requirements.

Produces intent_directive_instructions + intent_directive_context in the same format
that ir_builder already parses (JOIN_PATH:, TIME_FILTER:, SCHEMA_GAP: etc.)

This is the ONLY node that reasons about COMPUTATION/COMPUTED_FILTER/CTE logic.
All other agents see the directive as an authoritative spec — they do not re-derive it.
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_refinement_section, parse_tag
from app.services.agents.prompts import DIRECTIVE_WRITER_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


def _build_anchor_schema_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    if not columns:
        return "(no schema loaded)"

    by_table: dict[str, list[dict]] = {}
    for c in columns:
        fqn = c.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(c)

    lines = []
    for fqn, cols in by_table.items():
        lines.append(f"\n{fqn}:")
        for c in cols:
            name = c.get("name", "")
            dtype = c.get("data_type") or c.get("semantic_type", "")
            desc = (c.get("description") or "")[:120]
            grain = c.get("temporal_grain", "")
            ref_table = (c.get("referenced_table_fqn") or "").strip()
            col_line = f"  {name}  [{dtype}]{' temporal_grain=' + grain if grain and grain != 'none' else ''}"
            if ref_table:
                col_line += f"  [FK → {ref_table}]"
            lines.append(col_line)
            if desc:
                lines.append(f"    {desc}")
    return "\n".join(lines)


def _format_measures(measures: list[dict]) -> str:
    if not measures:
        return "(none)"
    return ", ".join(
        f"{m.get('table_fqn', '')}.{m.get('column_name', '')} ({m.get('aggregation', 'SUM')})"
        for m in measures
    )


def _format_filters(filters: list[dict], timeframe: str | None, time_filter_col: str | None) -> str:
    parts = []
    if timeframe:
        parts.append(f"timeframe={timeframe}" + (f" on {time_filter_col}" if time_filter_col else ""))
    for f in filters:
        parts.append(f"{f.get('table_fqn', '')}.{f.get('column_name', '')} {f.get('operator', '=')} '{f.get('raw_value', '')}'")
    return ", ".join(parts) if parts else "(none)"


def _format_dimensions(dimensions: list[dict]) -> str:
    if not dimensions:
        return "(none)"
    return ", ".join(
        f"{d.get('table_fqn', '')}.{d.get('column_name', '')} as {d.get('alias', d.get('column_name', ''))}"
        for d in dimensions
    )


def _compute_required_tables(
    measures: list, dimensions: list, filters: list,
    time_filter_col: str | None, all_anchor_tables: list,
) -> list:
    """Return only tables that contribute to the SQL output.

    A table is required if:
    - It provides a measure column
    - It provides a dimension column
    - It provides the time-filter column AND is the same table as a measure/dimension table
    - It provides a filter column AND is the same table as a measure/dimension table

    Tables with no output columns and no join-confirmed filter role are excluded.
    This prevents the CTE planner from inventing EXISTS/bridge JOINs for irrelevant tables.
    """
    required: set[str] = set()
    for m in measures:
        if m.get("table_fqn"):
            required.add(m["table_fqn"])
    for d in dimensions:
        if d.get("table_fqn"):
            required.add(d["table_fqn"])
    # Time filter and regular filters: only include if they live on a measure/dim table
    output_tables = set(required)
    if time_filter_col:
        parts = time_filter_col.rsplit(".", 1)
        if len(parts) == 2 and parts[0] in output_tables:
            required.add(parts[0])
    for f in filters:
        fqn = f.get("table_fqn")
        if fqn and fqn in output_tables:
            required.add(fqn)
    return list(required) if required else list(all_anchor_tables)


def _build_query_plan_section(query_plan: dict | None, timeframe: str | None) -> str:
    if not query_plan:
        return ""
    lines = []
    time_period = query_plan.get("required_time_period")
    if time_period and not timeframe:
        lines.append(f'USER\'S EXPLICIT TIME PERIOD: "{time_period}"')
        lines.append("  ⚠ filter_specialist did NOT extract a timeframe (schema mismatch or UNABLE_TO_EXTRACT)")
        lines.append("  → YOU MUST emit TIME_FILTER and COMPUTED_FILTER for this period using the best date column from the schema above")
    elif time_period:
        lines.append(f'USER\'S EXPLICIT TIME PERIOD: "{time_period}" (filter_specialist extracted: {timeframe})')
    cols = query_plan.get("expected_output_cols") or []
    if cols:
        lines.append(f"USER'S REQUESTED OUTPUT COLUMNS: {', '.join(cols)}")
    if query_plan.get("is_detail_request"):
        lines.append("QUERY TYPE: DETAIL/LIST — do NOT add aggregation in COMPUTATION lines")
    return "\n".join(lines) if lines else ""


async def directive_writer(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("directive_writer START | thread={}", state.get("thread_id", ""))

    resolved_intent = state.get("resolved_intent") or {}
    enriched_schema = state.get("enriched_schema") or {}

    # Extract assembled intent fields
    anchor_tables = resolved_intent.get("anchor_tables") or state.get("anchor_tables_resolved") or []
    measures = resolved_intent.get("measures") or []
    filters = resolved_intent.get("filters") or []
    dimensions = resolved_intent.get("dimensions") or []
    result_shape = resolved_intent.get("result_shape", "table")
    query_intent = resolved_intent.get("intent") or ""
    query_complexity = resolved_intent.get("complexity") or "simple"

    # Get timeframe + time_filter_col — prefer resolved_intent (set by intent_assembler),
    # fall back to specialist_outputs for the non-assembler path.
    specialist_outputs = state.get("specialist_outputs") or []
    timeframe = resolved_intent.get("timeframe")
    temporal_grains = resolved_intent.get("temporal_grains") or []
    time_filter_col = resolved_intent.get("time_filter_col") or None
    for s in specialist_outputs:
        if s.get("type") == "filters":
            timeframe = timeframe or s.get("timeframe")
            time_filter_col = time_filter_col or s.get("time_filter_col") or None
            temporal_grains = temporal_grains or s.get("temporal_grains") or []
            break

    filter_hint = state.get("filter_directive_hint") or ""
    filter_hint_section = (
        f"\nFILTER VALUE MAPPINGS (from filter specialist — use for COMPUTED_FILTER):\n{filter_hint}"
        if filter_hint else ""
    )

    required_tables = _compute_required_tables(measures, dimensions, filters, time_filter_col, anchor_tables)
    if len(required_tables) < len(anchor_tables):
        logger.info(
            "directive_writer | required_tables pruned | thread={} | all={} | required={}",
            state.get("thread_id"), anchor_tables, required_tables,
        )

    prompt = DIRECTIVE_WRITER_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        anchor_tables=", ".join(required_tables),
        measures=_format_measures(measures),
        filters=_format_filters(filters, timeframe, time_filter_col),
        dimensions=_format_dimensions(dimensions),
        result_shape=result_shape,
        timeframe=timeframe or "not specified",
        temporal_grains=", ".join(temporal_grains) if temporal_grains else "single",
        query_intent=query_intent,
        query_complexity=query_complexity,
        anchor_schema_section=_build_anchor_schema_section(enriched_schema),
        filter_hint_section=filter_hint_section,
        query_plan_section=_build_query_plan_section(state.get("query_plan"), timeframe),
        refinement_section=build_refinement_section(state, role="directive"),
        reasoning_directive=REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-directive-writer", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.error("directive_writer | LLM failed | thread={} | error={}", state.get("thread_id"), e)
        return {"intent_directive": "", "intent_directive_instructions": "", "intent_directive_context": ""}

    # Parse directive tags
    directive_raw = parse_tag(raw, "directive") or raw
    instructions_text = parse_tag(directive_raw, "instructions") or ""
    context_text = parse_tag(directive_raw, "context") or ""

    logger.info(
        "directive_writer DONE | thread={} | has_instructions={} | has_context={}",
        state.get("thread_id"), bool(instructions_text.strip()), bool(context_text.strip()),
    )

    return {
        "intent_directive": directive_raw,
        "intent_directive_instructions": instructions_text,
        "intent_directive_context": context_text,
    }

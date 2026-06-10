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
            ref_table = (c.get("referenced_table_fqn") or "").strip()
            col_line = f"  {name}  [{dtype}]"
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
    groupings = query_plan.get("required_groupings") or []
    if groupings:
        lines.append(f"USER'S REQUIRED GROUPINGS: {', '.join(groupings)}")
        lines.append("  ⚠ FEASIBILITY CHECK: For each required grouping, verify a column exists in anchor_tables")
        lines.append("    that provides this dimension AND can be joined to the primary cost/measure table.")
        lines.append("    If the cost/measure table has SCHEMA_GAP_JOIN to the grouping table → that measure CANNOT")
        lines.append("    be disaggregated by that grouping. Set CONFIDENCE < 0.4 and name the blocked groupings.")
    entities = query_plan.get("explicit_entities") or []
    if entities:
        lines.append(f"USER'S NAMED ENTITIES: {', '.join(entities)}")
        lines.append("  → verify these exist as valid filter values in the schema columns above;")
        lines.append("    flag in CONFIDENCE_NOTE if an entity has no matching column value")
    return "\n".join(lines) if lines else ""


def _build_concept_mappings_section(concept_mappings: dict | None) -> str:
    if not concept_mappings:
        return ""
    lines = ["CONCEPT MAPPINGS — business terms linked to anchor tables (emit COMPUTATION: not SCHEMA_GAP_CONCEPT):"]
    for term, info in concept_mappings.items():
        computation = info.get("computation") or ""
        definition = (info.get("definition") or "")[:120]
        table_fqn = info.get("table_fqn") or ""
        line = f"  {term}"
        if table_fqn:
            line += f"  [{table_fqn}]"
        if definition:
            line += f"  — {definition}"
        lines.append(line)
        if computation:
            lines.append(f"    COMPUTATION: {term.lower().replace(' ', '_')} = {computation}")
    return "\n".join(lines)


def _build_filter_columns_section(filters: list[dict], timeframe: str | None, time_filter_col: str | None) -> str:
    lines = []
    if time_filter_col and timeframe:
        lines.append(f"  {time_filter_col}  (time window: {timeframe})")
    for f in filters:
        fqn = f.get("table_fqn", "")
        col = f.get("column_name", "")
        val = f.get("raw_value") or f.get("raw_user_value", "")
        op = f.get("operator", "=")
        if fqn and col:
            lines.append(f"  {fqn}.{col} {op} '{val}'")
    return "\n".join(lines) if lines else "  (none)"


def _build_confirmed_join_paths_section(anchor_join_paths: list[dict] | None) -> str:
    if not anchor_join_paths:
        return ""
    lines = ["CONFIRMED JOIN PATHS (emit JOIN_PATH: for each — do NOT emit SCHEMA_GAP_JOIN for these pairs):"]
    for p in anchor_join_paths:
        from_fqn = p.get("from_fqn", "")
        to_fqn = p.get("to_fqn", "")
        clauses = p.get("join_clauses") or p.get("from_col") and [
            f"{from_fqn}.{p.get('from_col')} = {to_fqn}.{p.get('to_col')}"
        ] or []
        if clauses:
            lines.append(f"  {from_fqn} ↔ {to_fqn}")
            for clause in (clauses if isinstance(clauses, list) else [clauses]):
                lines.append(f"    JOIN_PATH: {clause}")
        else:
            lines.append(f"  {from_fqn} ↔ {to_fqn}  (join clause not available — emit JOIN_PATH with best matching columns)")
    return "\n".join(lines)


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

    logger.info("directive_writer | resolved_time_filter_col={} | timeframe={}", time_filter_col, timeframe)

    filter_hint = state.get("filter_directive_hint") or ""
    filter_hint_section = (
        f"\nFILTER VALUE MAPPINGS (from filter specialist — use for COMPUTED_FILTER):\n{filter_hint}"
        if filter_hint else ""
    )

    prompt = DIRECTIVE_WRITER_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        anchor_tables=", ".join(anchor_tables),
        measures=_format_measures(measures),
        filters=_format_filters(filters, timeframe, time_filter_col),
        dimensions=_format_dimensions(dimensions),
        result_shape=result_shape,
        timeframe=timeframe or "not specified",
        temporal_grains=", ".join(temporal_grains) if temporal_grains else "single",
        query_intent=query_intent,
        query_complexity=query_complexity,
        anchor_schema_section=_build_anchor_schema_section(enriched_schema),
        confirmed_join_paths_section=_build_confirmed_join_paths_section(state.get("anchor_join_paths")),
        concept_mappings_section=_build_concept_mappings_section(state.get("concept_mappings")),
        filter_hint_section=filter_hint_section,
        query_plan_section=_build_query_plan_section(state.get("query_plan"), timeframe),
        refinement_section=build_refinement_section(state, role="directive"),
        reasoning_directive=REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL,
        time_filter_col=time_filter_col or "not specified",
        filter_columns_section=_build_filter_columns_section(filters, timeframe, time_filter_col),
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

    # Log TIME_FILTER emission — authoritative column emitted to directive
    time_filter_emitted = None
    join_paths_emitted = 0
    for line in directive_raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TIME_FILTER:") and time_filter_emitted is None:
            time_filter_emitted = stripped
        if "JOIN_PATH:" in stripped.upper():
            join_paths_emitted += 1

    if time_filter_emitted:
        logger.info("directive_writer | time_filter_emitted | {}", time_filter_emitted)
    else:
        logger.warning("directive_writer | TIME_FILTER missing from directive | thread={}", state.get("thread_id"))

    logger.info("directive_writer | join_paths_emitted | count={}", join_paths_emitted)
    logger.info(
        "directive_writer DONE | thread={} | has_instructions={} | has_context={}",
        state.get("thread_id"), bool(instructions_text.strip()), bool(context_text.strip()),
    )

    return {
        "intent_directive": directive_raw,
        "intent_directive_instructions": instructions_text,
        "intent_directive_context": context_text,
    }

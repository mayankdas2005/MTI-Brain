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
            lines.append(f"  {name}  [{dtype}]{' temporal_grain=' + grain if grain and grain != 'none' else ''}")
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

    # Get timeframe + time_filter_col from specialist outputs
    specialist_outputs = state.get("specialist_outputs") or []
    timeframe = resolved_intent.get("timeframe")
    time_filter_col = None
    for s in specialist_outputs:
        if s.get("type") == "filters":
            timeframe = timeframe or s.get("timeframe")
            time_filter_col = s.get("time_filter_col")
            break

    prompt = DIRECTIVE_WRITER_PROMPT.format_messages(
        question=state["question"],
        anchor_tables=", ".join(anchor_tables),
        measures=_format_measures(measures),
        filters=_format_filters(filters, timeframe, time_filter_col),
        dimensions=_format_dimensions(dimensions),
        result_shape=result_shape,
        timeframe=timeframe or "not specified",
        anchor_schema_section=_build_anchor_schema_section(enriched_schema),
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

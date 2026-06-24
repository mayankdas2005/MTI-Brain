"""Node 1g: directive_writer — emit structured directives from assembled specialist intent.

Architecture (Phase 2):
  1. _build_directive_deterministic() — pure Python, no LLM. Converts structured
     specialist output fields directly to directive lines:
       TIME_FILTER  ← resolved_intent["time_filter_col"]   (filter_specialist)
       COMPUTATION  ← resolved_intent["derived_measures"]   (measure_specialist)
                   ← state["concept_mappings"]              (context_fetcher)
       COMPUTED_FILTER ← resolved_intent["threshold_specs"] (filter_specialist)
       MULTI_GRAIN  ← resolved_intent["temporal_grains"]   (filter_specialist)
       JOIN_PATH    ← state["anchor_join_paths"]            (ir_builder)
  2. _detect_schema_gaps() — single Haiku call. Identifies SCHEMA_GAP_* lines by
     comparing user intent against the loaded enriched schema.
  3. _compute_confidence() — rule-based. Degrades from 0.90 per gap/unresolved join.

Downstream nodes (sql_generator, ir_builder, schema_gap_resolver, confidence) read the
directive output format unchanged — same field names, same tag structure.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger

from app.services.agents.prompts import (
    REASONING_DIRECTIVE_DEEP,
    REASONING_DIRECTIVE_NORMAL,
    SCHEMA_GAP_DETECTOR_HUMAN,
    SCHEMA_GAP_DETECTOR_SYSTEM,
)
from app.services.agents.state import AnalyticsState


# ---------------------------------------------------------------------------
# Schema section builder (reused by gap detector Haiku call)
# ---------------------------------------------------------------------------

def _build_anchor_schema_section(enriched_schema: dict) -> str:
    columns = enriched_schema.get("columns") or []
    table_grains = enriched_schema.get("table_grains") or {}
    table_row_counts = enriched_schema.get("table_row_counts") or {}
    if not columns:
        return "(no schema loaded)"

    by_table: dict[str, list[dict]] = {}
    for c in columns:
        fqn = c.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(c)

    lines = []
    for fqn, cols in by_table.items():
        grain = table_grains.get(fqn, "")
        row_count = table_row_counts.get(fqn, 0)
        grain_note = f"  [grain: {grain[:100]}]" if grain else ""
        row_note = f"  rows={row_count:,}" if row_count else ""
        lines.append(f"\n{fqn}:{grain_note}{row_note}")
        for c in cols:
            name = c.get("name", "")
            sem = c.get("semantic_type") or c.get("data_type", "")
            desc = (c.get("description") or "")[:120]
            ref_table = (c.get("referenced_table_fqn") or "").strip()
            col_line = f"  [{sem}] {name}"
            if ref_table:
                col_line += f"  [FK -> {ref_table}]"
            lines.append(col_line)
            if desc:
                lines.append(f"    {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers (used by prompt sections and gap detector input)
# ---------------------------------------------------------------------------

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
        lines.append("  WARNING: filter_specialist did NOT extract a timeframe (schema mismatch or UNABLE_TO_EXTRACT)")
        lines.append("  ACTION: YOU MUST emit TIME_FILTER and COMPUTED_FILTER for this period using the best date column from the schema above")
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
        lines.append("  FEASIBILITY CHECK: For each required grouping, verify a column exists in anchor_tables")
        lines.append("    that provides this dimension AND can be joined to the primary cost/measure table.")
        lines.append("    If the cost/measure table has SCHEMA_GAP_JOIN to the grouping table, that measure CANNOT")
        lines.append("    be disaggregated by that grouping. Set CONFIDENCE < 0.4 and name the blocked groupings.")
    entities = query_plan.get("explicit_entities") or []
    if entities:
        lines.append(f"USER'S NAMED ENTITIES: {', '.join(entities)}")
        lines.append("  ACTION: verify these exist as valid filter values in the schema columns above;")
        lines.append("    flag in CONFIDENCE_NOTE if an entity has no matching column value")
    return "\n".join(lines) if lines else ""


def _build_confirmed_join_paths_section(anchor_join_paths: list[dict] | None) -> str:
    if not anchor_join_paths:
        return ""
    lines = ["CONFIRMED JOIN PATHS (these pairs have confirmed FK paths — do NOT emit SCHEMA_GAP_JOIN for them):"]
    for p in anchor_join_paths or []:
        from_fqn = p.get("from_fqn", "")
        to_fqn = p.get("to_fqn", "")
        clauses = p.get("join_clauses") or (
            [f"{from_fqn}.{p.get('from_col')} = {to_fqn}.{p.get('to_col')}"]
            if p.get("from_col") else []
        )
        if clauses:
            lines.append(f"  {from_fqn} ↔ {to_fqn}")
            for clause in (clauses if isinstance(clauses, list) else [clauses]):
                lines.append(f"    {clause}")
        else:
            lines.append(f"  {from_fqn} ↔ {to_fqn}  (join clause unavailable)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic directive handlers — one function per directive line type
# ---------------------------------------------------------------------------

def _emit_time_filter(resolved: dict) -> str | None:
    """TIME_FILTER from filter_specialist's authoritative time_filter_col.

    Source: resolved_intent["time_filter_col"] — set by filter_specialist, assembled by intent_assembler.
    Single authoritative source; Python cannot emit duplicates (no M17 needed).
    """
    col = (resolved.get("time_filter_col") or "").strip()
    return f"TIME_FILTER: {col}" if col else None


def _emit_computations(resolved: dict, concept_mappings: dict | None) -> list[str]:
    """COMPUTATION lines from two sources:

    1. resolved_intent["derived_measures"] — from MEASURE_SPECIALIST_PROMPT output.
       Each item: {alias: str, expression: SQL str, aggregation: str}
       expression is always SQL (e.g., "SUM(inflows) - SUM(outflows)"), never natural language.
    2. state["concept_mappings"] — business term → {computation: SQL expr, ...} from context_fetcher.
    """
    lines: list[str] = []
    for dm in resolved.get("derived_measures") or []:
        alias = (dm.get("alias") or "").strip()
        expr = (dm.get("expression") or "").strip()
        if alias and expr:
            lines.append(f"COMPUTATION: {alias} = {expr}")
    for term, mapping in (concept_mappings or {}).items():
        comp = ((mapping or {}).get("computation") or "").strip()
        if comp:
            key = term.lower().replace(" ", "_")
            lines.append(f"COMPUTATION: {key} = {comp}")
    return lines


def _emit_computed_filters(resolved: dict) -> list[str]:
    """COMPUTED_FILTER lines from resolved_intent["threshold_specs"].

    Source: FILTER_SPECIALIST_PROMPT output, field threshold_specs[].
    Each item: {expression: str, operator: str, value: numeric, label: str, is_having: bool}
    is_having=True  → HAVING clause (post-aggregation threshold)
    is_having=False → WHERE clause (pre-aggregation filter)
    Only emitted for CONDITION="Highlight" queries; empty list otherwise.
    """
    lines: list[str] = []
    for spec in resolved.get("threshold_specs") or []:
        expr = (spec.get("expression") or "").strip()
        op   = (spec.get("operator") or "").strip()
        val  = spec.get("value")
        if expr and op and val is not None:
            clause = "HAVING" if spec.get("is_having") else "WHERE"
            lines.append(f"COMPUTED_FILTER: {clause} {expr} {op} {val}")
    return lines


def _emit_multi_grain(resolved: dict) -> str | None:
    """MULTI_GRAIN when 2+ temporal grains requested.

    Source: resolved_intent["temporal_grains"] from filter_specialist.
    Single grain (or empty) → None (no line emitted).
    """
    grains = [g for g in (resolved.get("temporal_grains") or []) if g]
    return f"MULTI_GRAIN: {'+'.join(grains)}" if len(grains) >= 2 else None


def _emit_join_paths(anchor_join_paths: list[dict]) -> list[str]:
    """JOIN_PATH lines from ir_builder's resolved join clauses.

    Source: state["anchor_join_paths"] — each path has join_clauses: list[str].
    These are confirmed FK paths for Tier B join fallback in ir_builder.
    """
    lines: list[str] = []
    for path in anchor_join_paths or []:
        for clause in (path.get("join_clauses") or []):
            clause_str = (clause or "").strip()
            if clause_str:
                lines.append(f"JOIN_PATH: {clause_str}")
    return lines


def _build_directive_deterministic(state: dict) -> tuple[str, str]:
    """Build directive text from structured specialist outputs. No LLM, no network calls.

    Returns (instructions_text, context_text) — same structure consumed by downstream nodes.
    Missing fields emit nothing; never raises KeyError.
    """
    resolved = state.get("resolved_intent") or {}
    concept_mappings = state.get("concept_mappings") or {}
    anchor_join_paths = state.get("anchor_join_paths") or []

    instructions: list[str] = list(filter(None, [
        _emit_time_filter(resolved),
        *_emit_computations(resolved, concept_mappings),
        *_emit_computed_filters(resolved),
        _emit_multi_grain(resolved),
    ]))

    anchors = resolved.get("anchor_tables") or []
    shape = resolved.get("result_shape", "")
    context: list[str] = [
        *_emit_join_paths(anchor_join_paths),
        *(["ANCHOR_TABLES: " + ", ".join(anchors)] if anchors else []),
        *(["RESULT_SHAPE: " + shape] if shape else []),
    ]

    return "\n".join(instructions), "\n".join(context)


# ---------------------------------------------------------------------------
# Schema gap detector — Haiku, one job only
# ---------------------------------------------------------------------------

def _compute_confidence(gap_text: str, anchor_join_paths: list) -> str:
    """Rule-based confidence score: degrade from 0.90 per schema gap and unresolved join."""
    n_gaps = sum(1 for l in gap_text.splitlines() if l.strip().startswith("SCHEMA_GAP"))
    n_unresolved = sum(1 for p in (anchor_join_paths or []) if not (p.get("join_clauses") or []))
    score = max(0.40, 0.90 - 0.10 * n_gaps - 0.05 * n_unresolved)
    return f"CONFIDENCE_NOTE: {score:.2f} ({n_gaps} schema gaps, {n_unresolved} unresolved joins)"


async def _detect_schema_gaps(state: dict, config: RunnableConfig) -> str:
    """Haiku call: identify SCHEMA_GAP_* lines by comparing intent against loaded schema.

    Input:
      - enriched_schema (schema_enricher) → column visibility for gap detection
      - query_intent (intake_classifier) → what user actually asked for
      - anchor_join_paths (ir_builder) → which table pairs have confirmed FK paths
      - query_plan (query_planner) → explicit output/grouping contracts
      - deep_analysis → reasoning depth selector

    Output: ONLY SCHEMA_GAP_JOIN / SCHEMA_GAP_TABLE / SCHEMA_GAP_CONCEPT lines, or "".
    Non-fatal: returns "" on any exception.
    """
    resolved = state.get("resolved_intent") or {}
    enriched_schema = state.get("enriched_schema") or {}
    anchor_join_paths = state.get("anchor_join_paths") or []
    query_intent_lines = state.get("query_intent") or []
    query_plan = state.get("query_plan") or {}
    deep_analysis = state.get("deep_analysis", False)

    schema_section = _build_anchor_schema_section(enriched_schema)
    confirmed_joins_section = _build_confirmed_join_paths_section(anchor_join_paths)
    query_plan_section = _build_query_plan_section(query_plan, resolved.get("timeframe"))

    anchors = resolved.get("anchor_tables") or []
    measures = resolved.get("measures") or []
    dimensions = resolved.get("dimensions") or []
    temporal_grains = resolved.get("temporal_grains") or []
    intent_summary = (
        f"ANCHOR TABLES: {', '.join(anchors)}\n"
        f"MEASURES: {_format_measures(measures)}\n"
        f"DIMENSIONS: {_format_dimensions(dimensions)}\n"
        f"TEMPORAL GRAINS: {', '.join(temporal_grains) if temporal_grains else 'single'}\n"
        f"QUERY INTENT LINES:\n"
        + ("\n".join(query_intent_lines) if query_intent_lines else "(none)")
    )

    reasoning = REASONING_DIRECTIVE_DEEP if deep_analysis else REASONING_DIRECTIVE_NORMAL

    try:
        from app.services.agents.bedrock import get_llm
        from app.core.circuit_breaker import llm_breaker

        llm = get_llm("fast")

        from app.services.agents.helpers import build_instructions_section
        prompt = [
            SystemMessage(content=SCHEMA_GAP_DETECTOR_SYSTEM.format(
                intent_summary=intent_summary,
                anchor_schema_section=schema_section,
                confirmed_join_paths_section=confirmed_joins_section,
                query_plan_section=query_plan_section,
                reasoning_directive=reasoning,
                instructions_section=build_instructions_section(state, "schema gap detector"),
            )),
            HumanMessage(content=SCHEMA_GAP_DETECTOR_HUMAN),
        ]
        from app.services.agents.helpers import format_prior_context_block
        _prior_ctx_block = format_prior_context_block(
            state.get("prior_context_window") or state.get("prior_execution_context")
        )
        if _prior_ctx_block:
            prompt[0].content = _prior_ctx_block + "\n" + prompt[0].content

        @llm_breaker
        async def _call():
            from app.core.retry import retry_async
            return await retry_async(
                lambda: llm.ainvoke(prompt, config=config),
                service="bedrock-gap-detector",
                max_attempts=2,
                backoff_base=5.0,
            )

        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""

        # Keep ONLY SCHEMA_GAP_* lines — filter out everything else
        gap_lines = [
            l.strip() for l in raw.splitlines()
            if l.strip().startswith("SCHEMA_GAP_")
        ]
        return "\n".join(gap_lines)

    except Exception as e:
        logger.warning("directive_writer | gap_detector_failed | error={}", e)
        return ""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

async def directive_writer(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("directive_writer START | thread={}", state.get("thread_id", ""))

    # Step 1: deterministic directive from structured specialist outputs (no LLM)
    instructions_text, context_text = _build_directive_deterministic(state)

    # Step 2: Haiku gap detection (non-fatal — returns "" on failure)
    gap_text = await _detect_schema_gaps(state, config)

    # Step 3: rule-based confidence score
    anchor_join_paths = state.get("anchor_join_paths") or []
    confidence_line = _compute_confidence(gap_text, anchor_join_paths)

    # Assemble context: join_paths + anchor echo + gap lines + confidence
    context_parts = list(filter(None, [context_text, gap_text, confidence_line]))
    full_context_text = "\n".join(context_parts)

    directive = (
        "<directive>\n"
        f"<instructions>\n{instructions_text}\n</instructions>\n"
        f"<context>\n{full_context_text}\n</context>\n"
        "</directive>"
    )

    # Build directive_summary: all meaningful directive lines from instructions_text + SCHEMA_GAP lines.
    # Captures TIME_FILTER, COMPUTATION, COMPUTED_FILTER, MULTI_GRAIN — so even a simple SUM query
    # (which has no COMPUTATION lines) still produces a non-empty summary via its TIME_FILTER line.
    _SUMMARY_PREFIXES = ("TIME_FILTER:", "COMPUTATION:", "COMPUTED_FILTER:", "MULTI_GRAIN:")
    _directive_summary_lines = [
        l.strip() for l in instructions_text.splitlines()
        if any(l.strip().startswith(p) for p in _SUMMARY_PREFIXES)
    ] + [
        l.strip() for l in gap_text.splitlines()
        if l.strip().startswith("SCHEMA_GAP")
    ]
    directive_summary = "\n".join(_directive_summary_lines)

    # Log TIME_FILTER emission — authoritative column for all downstream date filtering
    tf_line = next(
        (l for l in instructions_text.splitlines() if l.strip().upper().startswith("TIME_FILTER:")),
        None,
    )
    if tf_line:
        logger.info("directive_writer | time_filter_emitted | {}", tf_line)
    else:
        logger.warning("directive_writer | TIME_FILTER missing from directive | thread={}", state.get("thread_id"))

    n_gaps = sum(1 for l in gap_text.splitlines() if l.strip().startswith("SCHEMA_GAP"))
    logger.info(
        "directive_writer DONE | thread={} | instructions_lines={} | schema_gaps={} | directive_summary_lines={}",
        state.get("thread_id"),
        len([l for l in instructions_text.splitlines() if l.strip()]),
        n_gaps,
        len(_directive_summary_lines),
    )

    import re as _re

    def _extract_col_tokens(text: str) -> list[str]:
        tokens = _re.findall(r'\b[a-z][a-z0-9_]{2,}\b', text or "")
        _stop = {"the", "for", "and", "not", "use", "sum", "avg", "max", "min", "count", "with",
                 "from", "where", "join", "group", "order", "null", "case", "when", "then", "else",
                 "are", "all", "any", "has", "its", "was", "per", "this", "that", "each", "into",
                 "only", "over", "also", "most", "last", "base", "data", "type", "name", "date",
                 "true", "false", "none", "both", "via", "used", "include", "such"}
        return list(dict.fromkeys(t for t in tokens if t not in _stop))[:8]

    _tf_period = ""
    for _tfl in instructions_text.splitlines():
        if _tfl.strip().upper().startswith("TIME_FILTER:"):
            _tf_period = _tfl.split(":", 1)[1].strip()
            break

    _intent_fingerprint: dict = {
        "anchor_tables": sorted(state.get("anchor_tables_resolved") or []),
        "measures":      _extract_col_tokens(state.get("_measure_specialist_output") or ""),
        "filters":       _extract_col_tokens(state.get("filter_directive_hint") or ""),
        "dimensions":    _extract_col_tokens(state.get("_dimension_specialist_output") or ""),
        "time_period":   _tf_period,
    }

    return {
        "intent_directive": directive,
        "intent_directive_instructions": instructions_text,
        "intent_directive_context": full_context_text,
        "_directive_summary": directive_summary,
        "intent_fingerprint": _intent_fingerprint,
    }

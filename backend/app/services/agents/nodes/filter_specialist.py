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
    build_mission_context,
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

    table_grains = enriched_schema.get("table_grains") or {}
    table_row_counts = enriched_schema.get("table_row_counts") or {}

    header_lines = [
        "FILTERABLE COLUMNS — known_values listed are DB enum codes (reference only).",
        "Your raw_user_value MUST be the user's exact words. The downstream resolver maps them to DB codes.",
        "",
    ]
    lines = header_lines[:]

    capped_by_table: dict = {}
    for c in capped:
        capped_by_table.setdefault(c.get("table_fqn", ""), []).append(c)

    for fqn, tbl_cols in capped_by_table.items():
        grain = table_grains.get(fqn, "")
        row_count = table_row_counts.get(fqn, 0)
        grain_note = f"  [grain: {grain[:100]}]" if grain else ""
        row_note = f"  rows={row_count:,}" if row_count else ""
        lines.append(f"{fqn}{grain_note}{row_note}")
        for c in tbl_cols:
            name = c.get("name", "")
            sem = c.get("semantic_type") or c.get("data_type", "")
            desc = (c.get("description") or "")[:200]
            synonyms = c.get("synonyms") or []
            distinct_vals = c.get("distinct_values") or c.get("value_vocabulary") or []
            value_aliases = c.get("value_aliases") or []
            sample_vals = (c.get("sample_values") or [])[:5]
            n_distinct = c.get("n_distinct") or -1

            sem_lower = (sem or "").lower()
            is_date_type = "date" in sem_lower or "timestamp" in sem_lower
            time_label = " [time-filter eligible]" if is_date_type else ""

            lines.append(f"  [{sem}] {name}{time_label}")
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
            else:
                _dt_lower = (c.get("data_type") or "").lower()
                _sem_type = (c.get("semantic_type") or "").lower()
                _is_string = "char" in _dt_lower or "varchar" in _dt_lower or "text" in _dt_lower
                _is_categorical = _is_string or _sem_type in {
                    "code", "dimension", "category", "flag", "status", "identifier",
                }
                if _is_categorical:
                    lines.append("    ⚠ NO KNOWN VALUES — 0 distinct values found for this column.")
                    lines.append("      This column is likely all-NULL in the source system.")
                    lines.append("      Filtering or joining on it will most likely return 0 rows.")
                    lines.append("      If required by the question: emit SCHEMA_GAP. Do NOT guess a value.")
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
            f'  User said "{eh.get("token")}" -- filter on {eh.get("table_fqn")}.{eh.get("column")}'
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

    # ── Prior pattern injection ─────────────────────────────────────────────────
    _pat  = _sc.get("_matched_pattern")
    _pat2 = _sc.get("_matched_pattern_second")
    _anti = _sc.get("_matched_anti_patterns") or []
    _tier = _sc.get("_matched_pattern_tier")

    prior_verified_section = ""
    prior_trace_row = ""

    if _pat and _tier in ("exact", "strong") and _pat.get("filter_summary"):
        _corroboration = ""
        if _pat2 and _pat2.get("filter_summary"):
            _corroboration = f"\nCORROBORATED BY 2nd pattern: {_pat2['filter_summary']}"
        _anti_sql_lines = "\n".join(
            f"Prior SQL error: {a.get('error_type', 'error')} on {a.get('failing_element', 'unknown')}"
            for a in _anti if a.get("error_type")
        )
        verb = "EXACT MATCH" if _tier == "exact" else "STRONG MATCH"
        prior_verified_section = (
            f"<prior_pattern>\n"
            f"Similar question: \"{_pat.get('question_text', '')}\"\n"
            f"{verb} — Prior verified filter interpretation:\n"
            f"  {_pat['filter_summary']}{_corroboration}\n"
            + (f"Note — similar questions had SQL errors (not interpretation errors):\n{_anti_sql_lines}\n" if _anti_sql_lines else "")
            + "Keep this in mind as you fill the GATE tables below. "
            "Adopt if the current question aligns. If different, explain the deviation in <reasoning>.\n"
            "</prior_pattern>"
        )
        prior_trace_row = (
            f"  | **[PRIOR — consider]** {_pat['filter_summary']} "
            f"| — | {_tier} match — adopt or explain deviation |\n"
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
        prior_verified_section=prior_verified_section,
        prior_trace_row=prior_trace_row,
    )
    _mission = build_mission_context(
        state,
        role="Identify all filter conditions, their schema columns, and the single time-filter column",
        feeds="intent_assembler → directive_writer (filter_intent, time_filter_col, temporal_grains)",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

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
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            raise ValueError(f"unexpected type {type(parsed).__name__}")
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
        f"{f.get('column_name')} ({f.get('table_fqn')}) raw='{f.get('raw_user_value')}'"
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

"""Node F: filter_resolver — resolves unresolved FilterSpecs.

Runs BEFORE SQL compilation. Tiers 1-3 are pure function (no I/O).
Tier 4 runs a Redshift DISTINCT probe. Tier 5 uses Haiku for disambiguation.
Tier 6 routes to clarification if all tiers fail.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.filter_resolver_logic import (
    build_redshift_probe_params,
    build_redshift_probe_sql,
    resolve_tier1_exact,
    resolve_tier2_fuzzy,
    resolve_tier3_temporal,
)
from app.services.neo4j_analytics.prompts import FILTER_DISAMBIGUATE_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.semantic_ir import FilterSpec, SemanticIR
from app.services.neo4j_analytics.sql_compiler import compile_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def filter_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("filter_resolver START | thread={}", state["thread_id"])

    ir_list = state.get("semantic_ir_list", [])
    if not ir_list:
        logger.warning("filter_resolver | no IR list | thread={}", state["thread_id"])
        return {"filter_resolution_needed": False}

    low_confidence_filters: list[dict] = list(state.get("low_confidence_filters") or [])
    clarification_needed = False
    clarification_reason = None
    updated_ir_list = []

    for ir_dict in ir_list:
        ir = SemanticIR(**ir_dict)
        updated_filters = []
        all_filters = list(ir.filters)
        if ir.time_filter and not ir.time_filter.resolved:
            all_filters.append(ir.time_filter)

        for f in all_filters:
            if f.resolved:
                updated_filters.append(f)
                continue

            resolved_f, low_confidence, needs_clarification, reason = await _resolve_filter(
                f, ir, state, config
            )

            if low_confidence:
                low_confidence_filters.append({
                    "column": f.column_name,
                    "raw_value": f.raw_user_value,
                    "resolved_value": resolved_f.value,
                })

            if needs_clarification:
                clarification_needed = True
                clarification_reason = reason

            updated_filters.append(resolved_f)

        time_filter = ir.time_filter
        regular_filters = [f for f in updated_filters if f != ir.time_filter]

        if ir.time_filter and not ir.time_filter.resolved:
            for f in updated_filters:
                if f.column_name == ir.time_filter.column_name:
                    time_filter = f
                    break

        updated_ir = ir.model_copy(update={
            "filters": regular_filters,
            "time_filter": time_filter,
        })
        updated_ir_list.append(updated_ir.model_dump())

    if clarification_needed:
        logger.info("filter_resolver | routing to clarification | thread={}", state["thread_id"])
        return {
            "semantic_ir_list": updated_ir_list,
            "needs_clarification": True,
            "clarification_reason": clarification_reason,
            "low_confidence_filters": low_confidence_filters,
            "filter_resolution_needed": False,
        }

    sql_list = []
    for ir_dict in updated_ir_list:
        ir = SemanticIR(**ir_dict)
        try:
            sql = compile_sql(ir)
            sql_list.append(sql)
        except Exception as e:
            logger.error("filter_resolver | SQL compile failed after resolution | thread={} | error={}", state["thread_id"], e)
            sql_list.append("")

    logger.info("filter_resolver DONE | thread={} | filters_resolved={}", state["thread_id"], len(updated_ir_list))
    return {
        "semantic_ir_list": updated_ir_list,
        "sql_list": sql_list,
        "low_confidence_filters": low_confidence_filters,
        "filter_resolution_needed": False,
        "needs_clarification": False,
    }


async def _resolve_filter(
    f: FilterSpec,
    ir: SemanticIR,
    state: AnalyticsState,
    config: RunnableConfig,
) -> tuple[FilterSpec, bool, bool, str | None]:
    """Try all tiers for a single FilterSpec.

    Returns (resolved_filter, is_low_confidence, needs_clarification, reason).
    """
    column_meta = _get_column_meta(f.table_fqn, f.column_name, state)
    sample_values = column_meta.get("sample_values") or []
    value_aliases = column_meta.get("value_aliases") or {}
    filter_selectivity = column_meta.get("filter_selectivity", "medium")

    # Tier 3 — temporal (try first for date columns)
    if column_meta.get("data_type", "").lower() in ("date", "timestamp", "datetime"):
        temporal = resolve_tier3_temporal(f.raw_user_value)
        if temporal:
            return f.model_copy(update={"operator": temporal["operator"], "value": temporal["value"], "resolved": True}), False, False, None

    # Tier 1 — exact match
    exact = resolve_tier1_exact(f.raw_user_value, sample_values, value_aliases)
    if exact:
        return f.model_copy(update={"value": exact, "resolved": True}), False, False, None

    # Tier 2 — fuzzy against sample_values
    fuzzy_val, fuzzy_score, candidates = resolve_tier2_fuzzy(f.raw_user_value, sample_values)
    if fuzzy_val and fuzzy_score >= 85 and not candidates:
        return f.model_copy(update={"value": fuzzy_val, "resolved": True}), False, False, None
    if fuzzy_val and 70 <= fuzzy_score < 85:
        return f.model_copy(update={"value": fuzzy_val, "resolved": True}), True, False, None

    # Tier 4 — Redshift DISTINCT probe (only for non-high-cardinality columns)
    if filter_selectivity != "high":
        probe_result = await _run_redshift_probe(f.table_fqn, f.column_name, f.raw_user_value, state["thread_id"])
        if probe_result:
            probe_fuzzy, probe_score, probe_candidates = resolve_tier2_fuzzy(f.raw_user_value, probe_result)
            if probe_fuzzy and probe_score >= 85 and not probe_candidates:
                return f.model_copy(update={"value": probe_fuzzy, "resolved": True}), False, False, None
            if probe_candidates:
                disambiguated = await _tier5_disambiguate(f, probe_candidates[:5], state, config)
                if disambiguated:
                    return f.model_copy(update={"value": disambiguated, "resolved": True}), False, False, None
            if not probe_result:
                reason = f"The value '{f.raw_user_value}' for `{f.column_name}` was not found in the data."
                return f, False, True, reason
    elif candidates:
        disambiguated = await _tier5_disambiguate(f, candidates[:5], state, config)
        if disambiguated:
            return f.model_copy(update={"value": disambiguated, "resolved": True}), False, False, None

    reason = (
        f"The value '{f.raw_user_value}' for `{f.column_name}` was not found. "
        f"Did you mean one of: {', '.join(candidates[:3])}?" if candidates else
        f"The value '{f.raw_user_value}' for `{f.column_name}` was not found in the data."
    )
    return f, False, True, reason


async def _run_redshift_probe(table_fqn: str, col_name: str, user_value: str, thread_id: str) -> list[str]:
    cached = redis_client.get_filter_values(table_fqn, col_name)
    if cached is not None:
        return cached

    try:
        from app.services.neo4j_analytics.redshift_client import execute_query
        sql = build_redshift_probe_sql(table_fqn, col_name, user_value)
        params = build_redshift_probe_params(user_value)
        columns, rows = await execute_query(sql, params=params, timeout_s=10, thread_id=thread_id)
        values = [str(r[0]) for r in rows if r and r[0] is not None]
        redis_client.set_filter_values(table_fqn, col_name, values, ttl=86400)
        return values
    except Exception as e:
        logger.warning("filter_resolver | probe failed | table={} col={} | error={}", table_fqn, col_name, e)
        return []


async def _tier5_disambiguate(f: FilterSpec, candidates: list[str], state: AnalyticsState, config: RunnableConfig) -> str | None:
    """Use Haiku to pick the best candidate."""
    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    prompt = FILTER_DISAMBIGUATE_PROMPT.format_messages(
        raw_user_value=f.raw_user_value,
        column_name=f.column_name,
        table_fqn=f.table_fqn,
        candidates="\n".join(f"- {c}" for c in candidates),
        question=state["question"],
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    try:
        response = await _call()
        raw = response.content or ""
        from json_repair import loads as json_loads
        output = parse_tag(raw, "output")
        if output:
            data = json_loads(output)
            return data.get("resolved_value")
    except Exception as e:
        logger.warning("filter_resolver | tier5 failed | error={}", e)
    return None


def _get_column_meta(table_fqn: str, col_name: str, state: AnalyticsState) -> dict:
    semantic_context = state.get("semantic_context") or {}
    for col in semantic_context.get("columns", []):
        if col.get("table_fqn") == table_fqn and col.get("name") == col_name:
            return col
    try:
        results = neo4j_client.resolve_columns(table_fqn, [col_name])
        return results[0] if results else {}
    except Exception:
        return {}

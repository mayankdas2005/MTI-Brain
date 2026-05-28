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
    resolve_tier1_combined,
    resolve_tier3_temporal,
)
from app.services.neo4j_analytics.prompts import FILTER_DISAMBIGUATE_PROMPT
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
    updated_ir_list = []

    for ir_dict in ir_list:
        ir = SemanticIR(**ir_dict)
        updated_filters = []
        time_filter_dropped = False

        all_filters = list(ir.filters)
        if ir.time_filter and not ir.time_filter.resolved:
            all_filters.append(ir.time_filter)

        for f in all_filters:
            if f.resolved:
                updated_filters.append(f)
                continue

            resolved_f, low_confidence = await _resolve_filter(f, ir, state, config)

            if resolved_f is None:
                if ir.time_filter and f.column_name == ir.time_filter.column_name:
                    time_filter_dropped = True
                continue

            if low_confidence:
                low_confidence_filters.append({
                    "column": f.column_name,
                    "raw_value": f.raw_user_value,
                    "resolved_value": resolved_f.value,
                })

            updated_filters.append(resolved_f)

        if time_filter_dropped:
            time_filter = None
            regular_filters = updated_filters
        else:
            time_filter = ir.time_filter
            regular_filters = [f for f in updated_filters if not (ir.time_filter and f.column_name == ir.time_filter.column_name)]
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
) -> tuple[FilterSpec | None, bool]:
    """Try all tiers for a single FilterSpec.

    Returns (resolved_filter, is_low_confidence).
    None means the filter should be dropped (column doesn't exist in schema).
    Never routes to clarification — always best-effort.
    """
    column_meta = _get_column_meta(f.table_fqn, f.column_name, state)

    if not column_meta:
        logger.warning(
            "filter_resolver | column not in schema | table={} col={} | dropping filter",
            f.table_fqn, f.column_name,
        )
        return None, False

    logger.debug(
        "filter_resolver | column_meta | table={} col={} | data_type={} semantic_type={}",
        f.table_fqn, f.column_name, column_meta.get("data_type"), column_meta.get("semantic_type"),
    )

    # filter_values = Redshift-probed distinct values written by context_fetcher enrichment
    # Never use Neo4j value_vocabulary (always []) or partial sample_values for matching
    filter_values = column_meta.get("filter_values") or []
    raw_aliases = column_meta.get("value_aliases")
    value_aliases = raw_aliases if isinstance(raw_aliases, dict) else {}
    filter_selectivity = column_meta.get("filter_selectivity", "medium")

    # Temporal (date columns and time_filter) — handle before value matching
    data_type = column_meta.get("data_type", "").lower()
    if data_type in ("date", "timestamp", "datetime") or f == ir.time_filter:
        temporal = resolve_tier3_temporal(f.raw_user_value)
        if temporal:
            return f.model_copy(update={
                "operator": temporal["operator"],
                "value": temporal["value"],
                "is_raw_sql": temporal.get("is_raw_sql", False),
                "resolved": True,
            }), False

    # Tier 1 Combined: aliases → exact → fuzzy (all against Redshift-probed filter_values)
    resolved_val, score, candidates = resolve_tier1_combined(f.raw_user_value, filter_values, value_aliases)
    if resolved_val and score >= 85 and not candidates:
        return f.model_copy(update={"value": resolved_val, "resolved": True}), False
    if resolved_val and 70 <= score < 85:
        return f.model_copy(update={"value": resolved_val, "resolved": True}), True

    # Tier 2: Live Redshift probe — only when filter_values is empty (enrichment didn't run)
    if not filter_values and filter_selectivity != "high":
        probe_result = await _run_redshift_probe(f.table_fqn, f.column_name, f.raw_user_value, state["thread_id"])
        if probe_result:
            resolved_val, score, candidates = resolve_tier1_combined(f.raw_user_value, probe_result, value_aliases)
            if resolved_val and score >= 85 and not candidates:
                return f.model_copy(update={"value": resolved_val, "resolved": True}), False
            if resolved_val and 70 <= score < 85:
                return f.model_copy(update={"value": resolved_val, "resolved": True}), True

    # Tier 3: LLM disambiguation for genuinely ambiguous candidates
    if candidates:
        disambiguated = await _tier5_disambiguate(f, candidates[:5], state, config)
        if disambiguated:
            return f.model_copy(update={"value": disambiguated, "resolved": True}), False

    # Best-effort: proceed with raw value, flag as low confidence
    logger.warning(
        "filter_resolver | value unresolvable | table={} col={} val={} | using raw value",
        f.table_fqn, f.column_name, f.raw_user_value,
    )
    return f.model_copy(update={"resolved": True}), True


async def _run_redshift_probe(table_fqn: str, col_name: str, user_value: str, thread_id: str) -> list[str]:
    cached = redis_client.get_filter_values(table_fqn, col_name)
    if cached is not None:
        return cached

    try:
        from app.services.neo4j_analytics.redshift_client import execute_query
        sql = build_redshift_probe_sql(table_fqn, col_name, user_value)
        params = build_redshift_probe_params(user_value)
        columns, rows = await execute_query(sql, params=params, timeout_s=60, thread_id=thread_id)
        values = list(dict.fromkeys(str(r[0]) for r in rows if r and r[0] is not None))
        redis_client.set_filter_values(table_fqn, col_name, values, ttl=86400)
        return values
    except Exception as e:
        logger.warning("filter_resolver | probe failed | table={} col={} | error={}", table_fqn, col_name, e)
        return []


async def _tier5_disambiguate(f: FilterSpec, candidates: list[str], state: AnalyticsState, config: RunnableConfig) -> str | None:
    """Use Haiku to pick the best candidate."""
    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_BRIEF
    candidates_text = "\n".join(f'  {i + 1}. "{c}"' for i, c in enumerate(candidates))
    prompt = FILTER_DISAMBIGUATE_PROMPT.format_messages(
        raw_user_value=f.raw_user_value,
        column_name=f.column_name,
        table_fqn=f.table_fqn,
        candidates=candidates_text,
        question=state["question"],
        reasoning_directive=REASONING_DIRECTIVE_BRIEF,
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

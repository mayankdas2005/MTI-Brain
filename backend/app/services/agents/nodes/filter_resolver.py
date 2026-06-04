"""Node F: filter_resolver — resolves unresolved FilterSpecs.

Runs BEFORE SQL compilation. Tiers 1-3 are pure function (no I/O).
Tier 4 runs a Redshift DISTINCT probe. Tier 5 uses Haiku for disambiguation.
Tier 6 routes to clarification if all tiers fail.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json
import json_repair

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents import neo4j_client, redis_client
from app.services.agents.filter_resolver_logic import (
    build_redshift_probe_params,
    build_redshift_probe_sql,
    resolve_tier1_combined,
    resolve_tier3_temporal,
)
from app.services.agents.prompts import FILTER_DISAMBIGUATE_PROMPT, TEMPORAL_RESOLVE_PROMPT
from app.services.agents.semantic_ir import FilterSpec, SemanticIR
from app.services.agents.state import AnalyticsState


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

    anchor_tables = list({
        tbl
        for ir_dict in updated_ir_list
        for tbl in (ir_dict.get("anchor_tables") or [])
    })
    filter_directive = _build_filter_directive(updated_ir_list, low_confidence_filters, anchor_tables=anchor_tables)
    logger.info(
        "filter_resolver DONE | thread={} | filters_resolved={}\nFILTER DIRECTIVE:\n{}",
        state["thread_id"], len(updated_ir_list), filter_directive,
    )
    return {
        "semantic_ir_list": updated_ir_list,
        "low_confidence_filters": low_confidence_filters,
        "filter_directive": filter_directive,
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

    # Hard guarantee: replace any human-name alias value with DB code before all tiers.
    # Runs unconditionally — even on filters already marked resolved=True by ir_builder.
    f = _alias_normalize(f, column_meta)

    logger.debug(
        "filter_resolver | column_meta | table={} col={} | data_type={} semantic_type={}",
        f.table_fqn, f.column_name, column_meta.get("data_type"), column_meta.get("semantic_type"),
    )

    # filter_values is set from value_vocabulary in context_fetcher (no Redshift probe).
    # value_vocabulary = distinct_values[0..30] from ingestion pipeline.
    # sample_values (5-10 items) is NOT used — insufficient for filter resolution.
    filter_values = column_meta.get("filter_values") or []
    filter_selectivity = column_meta.get("filter_selectivity", "medium")

    # value_aliases stored as list in Neo4j: ["BRL -> Brazilian Real", "USD -> US Dollar"]
    # Parse to dict: {"BRL": "Brazilian Real", ...}
    # resolve_tier1_combined does reverse lookup: user says "Brazilian Real" → returns "BRL"
    raw_aliases = column_meta.get("value_aliases") or []
    if isinstance(raw_aliases, dict):
        value_aliases = raw_aliases
    elif isinstance(raw_aliases, list):
        value_aliases = {}
        for alias_str in raw_aliases:
            for sep in (" -> ", " → ", "->", "→"):
                if sep in str(alias_str):
                    parts = str(alias_str).split(sep, 1)
                    value_aliases[parts[0].strip()] = parts[1].strip()
                    break
    else:
        value_aliases = {}

    # Temporal (date columns and time_filter) — handle before value matching.
    # Sync pre-check handles today/yesterday/ISO dates. Everything else goes to
    # _tier35_temporal_llm (Haiku) which resolves any natural language temporal phrase.
    data_type = column_meta.get("data_type", "").lower()
    if data_type in ("date", "timestamp", "datetime") or f == ir.time_filter:
        temporal = resolve_tier3_temporal(f.raw_user_value)
        if temporal is None:
            temporal = await _tier35_temporal_llm(f.raw_user_value, state)
        if temporal:
            return f.model_copy(update={
                "operator": temporal["operator"],
                "value": temporal["value"],
                "is_raw_sql": temporal.get("is_raw_sql", False),
                "resolved": True,
            }), False

    # Boolean safety net: catches any boolean filter that slipped through ir_builder
    if "bool" in data_type:
        from app.services.agents.nodes.ir_builder import _resolve_boolean_value
        bool_val = _resolve_boolean_value(f.raw_user_value)
        logger.info(
            "filter_resolver | bool_safety_net | {}.{} | '{}' → {}",
            f.table_fqn, f.column_name, f.raw_user_value, bool_val,
        )
        return f.model_copy(update={"value": bool_val, "is_raw_sql": True, "resolved": True}), False

    # Tier 1 Combined: aliases → exact → fuzzy (all against Redshift-probed filter_values)
    resolved_val, score, candidates = resolve_tier1_combined(f.raw_user_value, filter_values, value_aliases)
    if resolved_val and score >= 85 and not candidates:
        return f.model_copy(update={"value": resolved_val, "resolved": True}), False
    if resolved_val and 70 <= score < 85:
        return f.model_copy(update={"value": resolved_val, "resolved": True}), True

    # Tier 4: Live Redshift probe — two cases:
    # 4a) vocabulary empty → probe to populate it (existing behavior)
    # 4b) vocabulary partial (has some values) but Tier 1 failed on a code/identifier column
    #     → probe to catch entities not in stored max-100 values
    # Skip free-text high-cardinality columns (descriptions, names) to avoid useless probes.
    _CATEGORICAL_SEMANTICS = frozenset({"dimension", "flag", "code", "identifier", "category"})
    _is_categorical = (
        column_meta.get("semantic_type", "") in _CATEGORICAL_SEMANTICS
        or "bool" in data_type
        or filter_selectivity not in ("high",)
    )
    _CODE_SEMANTICS = frozenset({"code", "identifier"})
    _partial_vocab = (
        bool(filter_values) and not resolved_val
        and column_meta.get("semantic_type", "") in _CODE_SEMANTICS
    )
    if (not filter_values and _is_categorical) or _partial_vocab:
        _probe_reason = "partial_vocabulary" if _partial_vocab else "empty_vocabulary"
        logger.warning(
            "filter_resolver | TIER4_REDSHIFT | {}.{} | reason={} | val={}",
            f.table_fqn, f.column_name, _probe_reason, f.raw_user_value,
        )
        probe_result = await _run_redshift_probe(f.table_fqn, f.column_name, f.raw_user_value, state["thread_id"])
        if probe_result:
            combined = list(dict.fromkeys([*filter_values, *probe_result]))
            resolved_val, score, candidates = resolve_tier1_combined(f.raw_user_value, combined, value_aliases)
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


async def _tier35_temporal_llm(raw_value: str, state: AnalyticsState) -> dict | None:
    """Tier 3.5: LLM resolves temporal expressions that resolve_tier3_temporal couldn't handle.

    Only fires for date/timestamp columns when Tier 3 (keyword map) returns None.
    Uses Haiku with a structured JSON prompt. Returns {operator, value, is_raw_sql} or None.
    """
    try:
        from app.services.agents.bedrock import get_llm
        llm = get_llm("fast")
        messages = TEMPORAL_RESOLVE_PROMPT.format_messages(expression=raw_value)
        response = await llm.ainvoke(messages)
        text = (response.content or "").strip()
        # Extract JSON from response — may be wrapped in markdown code fence
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        parsed = json_repair.loads(text)
        if not parsed.get("operator"):
            return None   # LLM said this is not a temporal expression
        op = parsed["operator"]
        if op == "BETWEEN_SQL":
            value = [str(parsed.get("start", "")), str(parsed.get("end", ""))]
        else:
            value = str(parsed.get("value", ""))
        if not value or (isinstance(value, list) and not all(value)):
            return None
        logger.info("filter_resolver | tier35_temporal_llm | '{}' → op={} val={}", raw_value, op, value)
        return {"operator": op, "value": value, "is_raw_sql": True}
    except Exception as e:
        logger.warning("filter_resolver | tier35_temporal_llm failed | val={} | error={}", raw_value, e)
        return None


async def _run_redshift_probe(table_fqn: str, col_name: str, user_value: str, thread_id: str) -> list[str]:
    cached = redis_client.get_filter_values(table_fqn, col_name)
    if cached is not None:
        return cached

    try:
        from app.services.agents.redshift_client import execute_query
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
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    from app.services.agents.prompts import REASONING_DIRECTIVE_BRIEF

    # Build alias map from column metadata to show business meanings alongside DB values
    column_meta = _get_column_meta(f.table_fqn, f.column_name, state)
    raw_al = column_meta.get("value_aliases") or []
    alias_map: dict[str, str] = {}
    if isinstance(raw_al, list):
        for s in raw_al:
            for sep in (" -> ", " → ", "->", "→"):
                if sep in str(s):
                    parts = str(s).split(sep, 1)
                    alias_map[parts[0].strip()] = parts[1].strip()
                    break
    elif isinstance(raw_al, dict):
        alias_map = raw_al

    candidates_text = "\n".join(
        f'  {i + 1}. "{c}"{f"  ({alias_map[c]})" if c in alias_map else ""}'
        for i, c in enumerate(candidates)
    )
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


def _alias_normalize(f: FilterSpec, column_meta: dict) -> FilterSpec:
    """Replace any human-name alias value with the DB code — runs on ALL filters unconditionally.

    value_aliases format in Neo4j: ["CLOSING -> Closing Balance", ...]
    key = DB code ("CLOSING"), value = human label ("Closing Balance").
    If the filter value matches a human label, replace it with the DB code.
    """
    raw_aliases = column_meta.get("value_aliases") or []
    alias_map: dict[str, str] = {}
    for a in raw_aliases:
        for sep in (" -> ", " → ", "->", "→"):
            if sep in str(a):
                parts = str(a).split(sep, 1)
                alias_map[parts[0].strip()] = parts[1].strip()
                break
    if not alias_map:
        return f

    def _fix(v: str) -> str:
        v_lower = v.lower()
        # Handle raw "CODE -> Human Name" format — extract the DB code from the left side
        if " -> " in v:
            code_part = v.split(" -> ")[0].strip()
            matched = next((k for k in alias_map if k.lower() == code_part.lower()), None)
            if matched:
                return matched
        # Normal human-label reverse lookup
        for db_code, human_name in alias_map.items():
            if human_name.lower() == v_lower:
                return db_code
        return v

    val = f.value
    if isinstance(val, str):
        fixed = _fix(val)
        if fixed != val:
            logger.info(
                "filter_resolver | alias_normalize | {}.{} | '{}' → '{}'",
                f.table_fqn, f.column_name, val, fixed,
            )
            return f.model_copy(update={"value": fixed, "resolved": True})
    elif isinstance(val, list):
        fixed_list = [_fix(str(v)) for v in val]
        if fixed_list != [str(v) for v in val]:
            return f.model_copy(update={"value": fixed_list})
    return f


def _build_filter_directive(
    updated_ir_list: list[dict],
    low_confidence_filters: list[dict],
    anchor_tables: list[str] | None = None,
) -> str:
    """Build a compact filter directive from resolved FilterSpecs.

    Lists every resolved filter with an annotation explaining HOW it was resolved
    and how much to trust it. Downstream agents (sql_generator, repair) use this
    as the authoritative, complete filter list — no extra WHERE/EXISTS should be added.
    """
    low_conf_cols = {f["column"] for f in (low_confidence_filters or [])}

    lines = ["RESOLVED_FILTERS:"]
    for ir_dict in updated_ir_list:
        ir = SemanticIR(**ir_dict)
        all_f = list(ir.filters)
        time_col = ir.time_filter.column_name if ir.time_filter else None
        if ir.time_filter and ir.time_filter.resolved:
            all_f.append(ir.time_filter)

        ir_anchors = set(ir.anchor_tables or [])
        effective_anchors = set(anchor_tables or []) or ir_anchors

        for f in all_f:
            if not f.resolved:
                continue
            col = f"{f.table_fqn}.{f.column_name}"
            is_time = (f.column_name == time_col)
            is_low = f.column_name in low_conf_cols
            is_non_anchor = bool(effective_anchors) and f.table_fqn not in effective_anchors

            if is_non_anchor:
                tag = "[WARNING: non-anchor table — skip this filter, do NOT add EXISTS subquery]"
            elif f.operator in ("BETWEEN", "BETWEEN_SQL"):
                tag = "[time — deterministic]" if is_time else "[exact — high confidence]"
            elif f.is_raw_sql:
                tag = "[time — deterministic]" if is_time else "[SQL expression]"
            elif is_low:
                tag = "[fuzzy match — low confidence, suspect if 0 rows]"
            else:
                tag = "[exact DB code — high confidence]"

            if f.operator in ("BETWEEN", "BETWEEN_SQL") and not is_non_anchor:
                v = f.value
                v0, v1 = (v[0], v[1]) if isinstance(v, list) else (v, v)
                lines.append(f"  {col} BETWEEN {v0} AND {v1}  {tag}")
            elif f.is_raw_sql and not is_non_anchor:
                lines.append(f"  {col} {f.operator} {f.value}  {tag}")
            else:
                if isinstance(f.value, list):
                    val_str = "IN (" + ", ".join(f"'{x}'" for x in f.value) + ")"
                    lines.append(f"  {col} {val_str}  {tag}")
                else:
                    lines.append(f"  {col} {f.operator} '{f.value}'  {tag}")

    lines.append("FILTER_LIST_COMPLETE: do not add WHERE/EXISTS beyond the above")
    low_cols = [f["column"] for f in (low_confidence_filters or [])]
    if low_cols:
        lines.append(f"LOW_CONFIDENCE_FILTERS: {', '.join(low_cols)} — check these first if 0 rows")

    return "\n".join(lines)


def _get_column_meta(table_fqn: str, col_name: str, state: AnalyticsState) -> dict:
    semantic_context = state.get("semantic_context") or {}

    # Fast path: O(1) lookup from _column_lookup (full untrimmed data from context_fetcher)
    lookup = semantic_context.get("_column_lookup")
    if lookup:
        col = lookup.get((table_fqn, col_name))
        if col:
            return col

    # Slow path: linear scan of display columns (fallback if _column_lookup missing)
    for col in semantic_context.get("columns", []):
        if col.get("table_fqn") == table_fqn and col.get("name") == col_name:
            return col

    # Last resort: direct Neo4j query
    try:
        results = neo4j_client.resolve_columns(table_fqn, [col_name])
        return results[0] if results else {}
    except Exception:
        return {}

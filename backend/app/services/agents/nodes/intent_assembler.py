"""Node 1f: intent_assembler — deterministic merge, defer=True.

Waits for all 3 parallel specialist outputs via LangGraph deferred execution.
Merges specialist_outputs into resolved_intent JSON (same format as old intent_resolver).

Falls back to single-call intent_resolver if any specialist completely failed.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents.state import AnalyticsState


def _dedup_columns(measures: list[dict], filters: list[dict], dimensions: list[dict]) -> list[dict]:
    """Remove from dimensions any column that is already in measures or filters."""
    measure_keys = {(m.get("table_fqn"), m.get("column_name")) for m in measures}
    filter_keys = {(f.get("table_fqn"), f.get("column_name")) for f in filters}
    blocked = measure_keys | filter_keys
    return [d for d in dimensions if (d.get("table_fqn"), d.get("column_name")) not in blocked]


async def intent_assembler(state: AnalyticsState) -> dict:
    thread_id = state.get("thread_id", "")
    logger.info("intent_assembler START | thread={}", thread_id)

    specialist_outputs = state.get("specialist_outputs") or []
    anchor_tables = state.get("anchor_tables_resolved") or []

    # Collect outputs by type
    by_type: dict[str, dict] = {}
    for s in specialist_outputs:
        t = s.get("type")
        if t and t not in by_type:
            by_type[t] = s

    measures = by_type.get("measures", {}).get("measures") or []
    filters_raw = by_type.get("filters", {}).get("filters") or []
    timeframe = by_type.get("filters", {}).get("timeframe")
    time_filter_col = by_type.get("filters", {}).get("time_filter_col") or ""
    # temporal_grains from filter specialist (plural list) takes precedence;
    # fall back to temporal_grain (singular) from older format.
    temporal_grains = by_type.get("filters", {}).get("temporal_grains") or []
    if not temporal_grains:
        tg = by_type.get("filters", {}).get("temporal_grain")
        temporal_grains = [tg] if tg else []
    dimensions_raw = by_type.get("dimensions", {}).get("dimensions") or []
    # Structural intent fields — may be empty if specialists don't output them yet
    derived_measures = by_type.get("measures", {}).get("derived_measures") or []
    threshold_specs = by_type.get("filters", {}).get("threshold_specs") or []
    filter_directive_hint = by_type.get("filters", {}).get("filter_directive_hint") or ""

    # Check if any specialist completely failed
    missing = [t for t in ("measures", "filters", "dimensions") if t not in by_type]
    if missing:
        logger.warning("intent_assembler | missing_specialist_outputs={} | will use fallback | thread={}", missing, thread_id)
        # Fallback: delegate to single-call intent_resolver (old pipeline path)
        return {"_intent_assembler_fallback": True}

    # Deduplicate: remove dimension columns that are already measures or filters
    dimensions = _dedup_columns(measures, filters_raw, dimensions_raw)

    # Normalize filter format to match what ir_builder expects
    filters = [
        {
            "table_fqn": f.get("table_fqn", ""),
            "column_name": f.get("column_name", ""),
            "operator": f.get("operator", "="),
            "raw_value": f.get("raw_user_value", f.get("value", "")),
        }
        for f in filters_raw
    ]

    # Extract result_shape from anchor_resolver output stored in resolved_intent
    existing = state.get("resolved_intent") or {}
    result_shape = existing.get("result_shape", "table")

    # Assemble resolved_intent in the same format ir_builder expects
    resolved_intent = {
        "anchor_tables": anchor_tables,
        "result_shape": result_shape,
        "measures": measures,
        "dimensions": dimensions,
        "filters": filters,
        "timeframe": timeframe,
        "time_filter_col": time_filter_col,
        "temporal_grains": temporal_grains,
        "intent": by_type.get("measures", {}).get("measure_directive", ""),
        "complexity": "complex" if len(measures) > 1 or len(dimensions) > 2 else "simple",
        "derived_measures": derived_measures,
        "threshold_specs": threshold_specs,
        "confidence": 0.75,  # will be refined by directive_writer's CONFIDENCE_NOTE
        "limit": state.get("max_rows", 100),
        "order_by": [],
        "template_id": existing.get("template_id", ""),
    }

    logger.info(
        "intent_assembler DONE | thread={} | anchor_tables={} | measures={} | dims={} | filters={} | timeframe={}",
        thread_id,
        anchor_tables,
        [m.get("column_name") for m in measures],
        [d.get("column_name") for d in dimensions],
        [f.get("column_name") for f in filters],
        timeframe,
    )

    return {
        "resolved_intent": resolved_intent,
        "filter_directive_hint": filter_directive_hint,
        "specialist_outputs": [],  # clear accumulated outputs for next run
    }

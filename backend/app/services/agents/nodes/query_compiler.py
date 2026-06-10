"""Node 2: query_compiler — builds SemanticIR from resolved intent.

Single-query only (decomposition removed).
Filter resolution happens BEFORE SQL compilation (routes to filter_resolver if needed).

Changes from original:
- Removed _probe_join_clause_columns() — no Redshift DISTINCT probes at compilation time
- ir/validation functions now validate against _column_lookup (Neo4j data) instead of Redshift
"""

from __future__ import annotations

import re as _re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.nodes.ir_builder import build_semantic_ir
from app.services.agents.ir import validation as ir_val
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState

# Split only on unambiguous list separators — NOT 'and'/'or' which are ambiguous
# ("USD and CAD" vs "$100M or more"). Preserves dates, FX pairs (USD/EUR), codes.
_LIST_SEP_RE = _re.compile(r"[,;|]")
_NUMERIC_START_RE = _re.compile(r"^[\$\-\+]?\d")


def _build_schema_directive(ir: SemanticIR, semantic_context: dict | None = None) -> str:
    """Compact code-verified structural summary from SemanticIR for downstream agents.

    Gives the sql_generator, CTE planner, and repair agent an authoritative spec of:
    - Which tables are the closed set (ANCHOR_TABLES)
    - The exact confirmed join ON clauses from Neo4j
    - Any unresolved pairs and their candidate columns
    - Compact list of measures and dimensions
    - Available columns on hub/bridge tables (prevents hallucinated column names)
    """
    lines = ["SCHEMA DIRECTIVE — code-verified structure from ir_builder:"]
    lines.append(f"ANCHOR_TABLES: {', '.join(ir.anchor_tables)}")
    lines.append(
        "  (SCHEMA AVAILABLE — include a table in the SQL ONLY IF at least one of its columns "
        "appears in FINAL SELECT, it is a confirmed bridge in JOIN_CHAIN, or it provides a "
        "required WHERE filter on the primary fact table. Tables with UNRESOLVED joins MUST be "
        "OMITTED entirely — never include them via EXISTS, bridge CTEs, or extra JOINs.)"
    )

    if ir.join_clauses:
        lines.append("JOIN_CHAIN (copy ON clauses verbatim):")
        for i, clause in enumerate(ir.join_clauses):
            if not clause:
                continue
            jtype = (ir.join_types[i] if i < len(ir.join_types) else "JOIN") or "JOIN"
            lines.append(f"  {jtype}: {clause}")
    else:
        lines.append("JOIN_CHAIN: single table — no joins needed")

    if ir.unresolved_join_pairs:
        lines.append(
            "UNRESOLVED_PAIRS — no confirmed join path found for these table pairs. "
            "OMIT them from the SQL entirely. Do NOT add them via EXISTS, IN subqueries, "
            "bridge CTEs, or extra JOINs. Forcing an unresolved join produces zero rows."
        )
        for p in ir.unresolved_join_pairs:
            candidates = ", ".join(p.get("candidate_join_columns") or [])
            lines.append(
                f"  {p['from']} ↔ {p['to']} — OMIT BOTH TABLES unless one has output columns"
                + (f" | candidate cols if a join path is later found: {candidates}" if candidates else "")
            )

    if ir.measures:
        m_strs = [f"{m.column_name} ({m.aggregation or 'SUM'})" for m in ir.measures]
        lines.append(f"MEASURES: {', '.join(m_strs)}")
    if ir.dimensions:
        d_strs = [
            d.column_name + (f" → {d.alias}" if d.alias and d.alias != d.column_name else "")
            for d in ir.dimensions
        ]
        lines.append(f"DIMENSIONS: {', '.join(d_strs)}")

    # Non-anchor tables in the path (hub, bridge) only carry their join-key columns.
    # Listing them here prevents the SQL generator from inventing columns that don't exist
    # (e.g. ba.company_ref on lpp.bank_account which only exposes 'code' as a join key).
    #
    # Safety rules:
    #   - Anchor tables are EXCLUDED — they are fully loaded and the SQL generator
    #     may use all their columns (no restriction needed).
    #   - The hub table is always injected into ir.anchor_tables by ir_builder, so
    #     it never appears here even when it's also in ir.path_tables.
    #   - Deduplicate to avoid emitting the same table twice (path_tables may repeat).
    if semantic_context:
        col_lookup: dict = semantic_context.get("_column_lookup") or {}
        anchor_set = set(ir.anchor_tables)
        seen_path: set[str] = set()
        for tbl in (ir.path_tables or []):
            if tbl in anchor_set or tbl in seen_path:
                continue
            seen_path.add(tbl)
            tbl_cols = sorted({col for fqn, col in col_lookup if fqn == tbl})
            if tbl_cols:
                lines.append(
                    f"HUB/BRIDGE TABLE {tbl} — available columns (join keys only): "
                    + ", ".join(tbl_cols)
                    + " — do NOT use any other column from this table"
                )

    # Anchor tables whose join clause failed validation — restrict their columns so the LLM
    # doesn't invent names that don't exist (e.g. ba.company_ref on bank_account which only
    # has 'code'). These tables are hub-injected anchors with no confirmed join path.
    if semantic_context and ir.unresolved_join_pairs:
        col_lookup_2: dict = semantic_context.get("_column_lookup") or {}
        anchor_set_2 = set(ir.anchor_tables)
        failed_anchors: set[str] = set()
        for p in ir.unresolved_join_pairs:
            for key in ("from", "to"):
                t = p.get(key, "")
                if t in anchor_set_2:
                    failed_anchors.add(t)
        for tbl in sorted(failed_anchors):
            tbl_cols = sorted({col for fqn, col in col_lookup_2 if fqn == tbl})
            if tbl_cols:
                lines.append(
                    f"FAILED-JOIN ANCHOR TABLE {tbl} — join validation failed; "
                    f"available columns: {', '.join(tbl_cols)} — do NOT use any other column from this table"
                )
            else:
                lines.append(
                    f"FAILED-JOIN ANCHOR TABLE {tbl} — join validation failed; no confirmed columns available"
                )

    # Explicitly invalid columns — hallucinated by intent specialists + proactively blocked.
    invalid = list(ir.hallucinated_columns or [])
    if invalid:
        lines.append(
            "INVALID COLUMNS — these do NOT exist on the tables above; "
            "do NOT use them under any name or on any other table:\n"
            + "\n".join(f"  ✗ {c}" for c in invalid)
        )

    # TEMPORAL COLUMNS: when multiple date columns exist on the primary anchor table,
    # expose all so directive_writer can pick the semantically correct one (instead of
    # deferring to whatever _find_date_column picked as the default first match).
    if semantic_context and ir.anchor_tables:
        primary_table = ir.anchor_tables[0]
        all_cols = semantic_context.get("columns") or []
        _DATE_DTYPES = {"date", "timestamp", "timestamptz", "timestamp with time zone",
                        "timestamp without time zone"}
        temporal_cols = [
            c for c in all_cols
            if c.get("table_fqn") == primary_table
            and any(dt in (c.get("data_type") or "").lower() for dt in ("date", "timestamp"))
        ]
        if len(temporal_cols) > 1:
            lines.append(
                "\nTEMPORAL COLUMNS on primary anchor — directive_writer: pick the one that is "
                "the logical time anchor for your computation (state your choice as "
                "\"time_filter: table.column\" in COMPUTATION):"
            )
            for c in temporal_cols:
                desc = (c.get("description") or "")[:80]
                col_name = c.get("name", "")
                dtype = c.get("data_type") or "date"
                desc_str = f"  {desc}" if desc else ""
                lines.append(f"  {primary_table}.{col_name}   [{dtype}]{desc_str}")

    return "\n".join(lines)


def _split_multi_value_filters(ir: SemanticIR) -> SemanticIR:
    """Split filters like 'USD, CAD' into separate FilterSpec objects.

    Only splits on comma, semicolon, pipe. Skips if any part starts with a digit or
    currency symbol (numeric/currency values must not be split on comma thousands-separators).
    """
    expanded = []
    for f in ir.filters:
        if f.resolved or f.is_raw_sql:
            expanded.append(f)
            continue
        parts = [p.strip() for p in _LIST_SEP_RE.split(f.raw_user_value) if p.strip()]
        if len(parts) <= 1:
            expanded.append(f)
            continue
        if any(_NUMERIC_START_RE.match(p) for p in parts):
            expanded.append(f)
            continue
        for part in parts:
            expanded.append(f.model_copy(update={"raw_user_value": part, "value": part}))
    return ir.model_copy(update={"filters": expanded})


async def query_compiler(state: AnalyticsState, config: RunnableConfig) -> dict:
    resolved = state.get("resolved_intent") or {}
    semantic_context = state.get("semantic_context") or {}

    anchor_tables = resolved.get("anchor_tables") or []
    logger.info(
        "query_compiler START | thread={} | anchor_tables={} | complexity={}",
        state["thread_id"], anchor_tables, resolved.get("complexity"),
    )

    known_fqns = {t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")}
    missing = [t for t in anchor_tables if t not in known_fqns]
    if missing:
        logger.warning(
            "query_compiler | anchor_tables not in semantic_context | missing={} | thread={}",
            missing, state["thread_id"],
        )

    logger.info(
        "query_compiler | intent | thread={} | anchor_tables={} | measures={} | dimensions={} | filters={} | timeframe={}",
        state["thread_id"],
        anchor_tables,
        [(m.get("table_fqn", "").rsplit(".", 1)[-1] + "." + m.get("column_name", ""), m.get("aggregation")) for m in resolved.get("measures", [])],
        [d.get("column_name") for d in resolved.get("dimensions", [])],
        [(f.get("column_name") or f.get("column"), f.get("operator"), str(f.get("raw_value", ""))[:20]) for f in resolved.get("filters", [])],
        resolved.get("timeframe"),
    )

    return await _handle_single(state, resolved, semantic_context, config)


async def _handle_single(
    state: AnalyticsState,
    resolved: dict,
    semantic_context: dict,
    config: RunnableConfig,
) -> dict:
    thread_id = str(state["thread_id"])

    try:
        ir = await build_semantic_ir(resolved, semantic_context, state)
    except Exception as e:
        logger.error("query_compiler | IR build failed | thread={} | error={}", thread_id, e)
        return {
            "error": str(e),
            "needs_clarification": True,
            "clarification_reason": "I couldn't map your question to the data model.",
        }

    # Split comma/semicolon/pipe-separated filter values into individual FilterSpecs.
    ir = _split_multi_value_filters(ir)

    # Ensure every measure has an aggregation — SUM is the safe default when intent_resolver
    # left it null (the LLM prompt says "always set" but this is the backstop).
    if any(not m.aggregation for m in ir.measures):
        patched_measures = [
            m.model_copy(update={"aggregation": m.aggregation or "SUM"})
            for m in ir.measures
        ]
        ir = ir.model_copy(update={"measures": patched_measures})
        logger.debug("query_compiler | null_aggregation_defaulted_to_SUM | thread={}", thread_id)

    # Validate and fix column refs + join clauses against Neo4j _column_lookup.
    # No Redshift calls — validation uses _column_lookup loaded in context_fetcher.
    try:
        ir = await ir_val.strip_hallucinated_columns(ir, thread_id, semantic_context)
        ir = await ir_val.validate_and_fix_join_clauses(ir, thread_id, semantic_context)
    except Exception as e:
        logger.warning("query_compiler | IR validation error (continuing) | thread={} | error={}", thread_id, e)

    has_unresolved = any(not f.resolved for f in ir.filters)
    if ir.time_filter and not ir.time_filter.resolved:
        has_unresolved = True

    # Build a filter_directive from the current IR so the sql_generator always receives one,
    # even when filter_resolver is skipped (all filters already resolved by ir_builder).
    # Covers time filters, numeric filters, and string exact-matches resolved in ir_builder.
    # When filter_resolver DOES run, it overwrites this with the fully-resolved version.
    from app.services.agents.nodes.filter_resolver import _build_filter_directive
    filter_directive = _build_filter_directive([ir.model_dump()], [], anchor_tables=ir.anchor_tables)
    if filter_directive:
        logger.info("query_compiler | FILTER DIRECTIVE | thread={}\n{}", thread_id, filter_directive)
    else:
        logger.warning("query_compiler | FILTER DIRECTIVE empty (no resolved filters yet) | thread={}", thread_id)

    schema_directive = _build_schema_directive(ir, semantic_context=semantic_context)
    logger.info("query_compiler | SCHEMA DIRECTIVE | thread={}\n{}", thread_id, schema_directive)

    if has_unresolved:
        logger.info("query_compiler | unresolved filters | routing to filter_resolver | thread={}", thread_id)
        return {
            "semantic_ir_list": [ir.model_dump()],
            "filter_resolution_needed": True,
            "filter_directive": filter_directive,
            "schema_directive": schema_directive,
        }

    logger.info("query_compiler | all filters resolved | routing to sql_generator | thread={}", thread_id)
    return {
        "semantic_ir_list": [ir.model_dump()],
        "filter_resolution_needed": False,
        "filter_directive": filter_directive,
        "schema_directive": schema_directive,
    }

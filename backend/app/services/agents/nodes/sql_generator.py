"""SQL generation via LLM for the query_compiler node.

Builds the spec dict from a SemanticIR, logs it fully, then calls the
SQL generation LLM and returns the raw SQL string.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

# UUID columns are unique per row — using them as join keys always returns 0 rows.
_UUID_SUFFIXES = ("_uuid", "_guid", "_uid")


def _is_uuid_col(col_name: str) -> bool:
    n = col_name.lower()
    return n == "uuid" or any(n.endswith(s) for s in _UUID_SUFFIXES)

from app.core.logger import logger
from app.services.agents.helpers import format_sql, parse_tag
from app.services.agents.nodes.schema_context import build_schema_context, fetch_anti_patterns, fetch_query_patterns
from app.services.agents.prompts import REASONING_DIRECTIVE_SQL, REASONING_DIRECTIVE_NORMAL, CTE_COLUMN_PLANNER_PROMPT
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


async def generate_sql_llm(
    ir: SemanticIR,
    semantic_context: dict,
    state: AnalyticsState,
    config: RunnableConfig,
) -> str:
    # On recompile, the IR/schema haven't changed — reuse the cached schema context
    # to avoid redundant Neo4j queries (build_schema_context + fetch_anti_patterns +
    # fetch_query_patterns all hit Neo4j; running them 3× per query is pure waste).
    recompile_count = state.get("recompile_count", 0)
    _cached = state.get("_sql_schema_ctx_cache") if recompile_count > 0 else None
    if _cached:
        schema_ctx_full = dict(_cached)
        logger.info("sql_generator | schema_ctx cache HIT | recompile={}", recompile_count)
    else:
        schema_ctx_full = build_schema_context(ir, semantic_context)
        # Store in state for subsequent retries (in-place mutation is safe here —
        # this is a private key not read by any LangGraph reducer)
        state["_sql_schema_ctx_cache"] = dict(schema_ctx_full)

    unresolved_pairs = schema_ctx_full.pop("_unresolved_pairs", [])
    schema_ctx = {k: v for k, v in schema_ctx_full.items()}

    spec = {
        "anchor_tables": ir.anchor_tables,
        "path_tables": ir.path_tables,
        "joins": [
            {
                "from": ir.path_tables[i],
                "to": ir.path_tables[i + 1],
                "type": ir.join_types[i] if i < len(ir.join_types) else "JOIN",
                "on": ir.join_clauses[i],
            }
            for i in range(len(ir.join_clauses))
            if ir.join_clauses[i]
            if i + 1 < len(ir.path_tables)
        ],
        "unresolved_anchor_pairs": unresolved_pairs,
        "measures": [m.model_dump() for m in ir.measures],
        "dimensions": [d.model_dump() for d in ir.dimensions],
        "filters": [
            {
                "table_fqn": f.table_fqn,
                "column": f.column_name,
                "operator": f.operator,
                "value": f.value,
                "is_having": f.is_having,
                "is_raw_sql": f.is_raw_sql,
                "resolved": f.resolved,
            }
            for f in ir.filters
        ],
        "time_filter": {
            "table_fqn": ir.time_filter.table_fqn,
            "column": ir.time_filter.column_name,
            "operator": ir.time_filter.operator,
            "value": ir.time_filter.value,
        } if ir.time_filter else None,
        "cte_steps": ir.cte_steps[:4],
        "result_shape": ir.result_shape,
        "order_by": ir.order_by,
        "limit": ir.limit or state.get("max_rows", 100),
        "temporal_grains": ir.temporal_grains,          # list, most granular first
        "temporal_grain": ir.temporal_grain,            # compat: first grain or None
        "unresolved_join_pairs": ir.unresolved_join_pairs or [],   # explicit failure signal
        "derived_measures": [dm.model_dump() for dm in (ir.derived_measures or [])],
        "threshold_specs":  [ts.model_dump() for ts in (ir.threshold_specs or [])],
    }

    logger.info(
        "sql_generator | LLM input | thread={} | anchor_tables={} | pre_loaded_joins={} | unresolved_pairs={} | measures={} | dimensions={} | filters={} | time_filter={}",
        state["thread_id"],
        spec["anchor_tables"],
        [(j["from"], j["to"], j["on"]) for j in spec["joins"]],
        [(p["from"], p["to"], p.get("candidate_join_columns")) for p in unresolved_pairs],
        [(m["column_name"], m.get("aggregation")) for m in spec["measures"]],
        [d["column_name"] for d in spec["dimensions"]],
        [(f["column"], f["operator"], str(f["value"])[:20]) for f in spec["filters"]],
        (
            f"{spec['time_filter']['column']} {spec['time_filter']['operator']} {str(spec['time_filter']['value'])[:30]}"
            if spec.get("time_filter") else None
        ),
    )

    # Also cache anti-patterns and query patterns — same Neo4j data on every retry
    if _cached and "_anti_patterns" in state:
        anti_patterns = state["_anti_patterns"]
        query_patterns, pattern_matched, pattern_name = (
            state.get("_query_patterns", []),
            state.get("_pattern_matched", False),
            state.get("_pattern_name"),
        )
    else:
        anti_patterns = await fetch_anti_patterns(state)
        query_patterns, pattern_matched, pattern_name = await fetch_query_patterns(state)
        state["_anti_patterns"] = anti_patterns
        state["_query_patterns"] = query_patterns
        state["_pattern_matched"] = pattern_matched
        state["_pattern_name"] = pattern_name

    logger.info(
        "sql_generator | context_injection | anti_patterns={} | query_pattern={} | thread={}",
        "injected" if anti_patterns != "(none)" else "none",
        pattern_name or "none",
        state.get("thread_id"),
    )

    # Build col_lookup first — needed by query_blueprint (filter routing), unresolved joins, and candidate paths.
    col_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": (
            c.get("filter_values") or c.get("value_vocabulary") or c.get("sample_values") or []
        )
        for c in schema_ctx.get("columns", [])
        if c.get("table_fqn") and c.get("name")
    }

    query_blueprint = _build_query_blueprint(spec, schema_ctx, col_lookup)
    schema_reference = _build_schema_reference(schema_ctx)
    unresolved_joins_section = _build_unresolved_joins_section(unresolved_pairs, col_lookup)
    feedback_section = _build_feedback_section(state)
    query_patterns_section = _build_query_patterns_section(query_patterns, pattern_matched, pattern_name)
    prior_sql_section = _build_prior_sql_section(state)

    recompile_count = state.get("recompile_count", 0)
    # Always show candidate join paths when Neo4j found alternatives — prevents the SQL generator
    # from discovering join problems only after first-attempt failure.
    candidate_join_paths_section = (
        _build_candidate_join_paths_section(ir, col_lookup) if ir.candidate_join_paths else ""
    )
    reasoning_directive = REASONING_DIRECTIVE_SQL

    # Cross-domain section — injected before QUERY SPECIFICATION when applicable
    cross_domain_section = _build_cross_domain_section(semantic_context)

    # Entity hints — pre-resolved DB codes from entity_value path; prevents LLM from
    # guessing filter values that were already matched against schema vocabulary.
    entity_hints_section = _build_entity_hints_section(schema_ctx)

    # Combined intent + filter directive — authoritative context from intent_resolver + filter_resolver
    from app.services.agents.helpers import build_directive_section
    directive_section = build_directive_section(state)
    directive_section += _build_low_confidence_section(state)

    state["_planner_ir"] = ir
    state["_planner_col_lookup"] = col_lookup
    cte_plan = await _plan_cte_columns(spec, query_blueprint, schema_reference, state, config, directive_section)
    cte_column_plan = _build_cte_plan_section(cte_plan)

    from app.services.agents.prompts import SQL_GENERATE_PROMPT
    prompt = SQL_GENERATE_PROMPT.format_messages(
        question=state.get("effective_question") or state.get("question", ""),
        cross_domain_section=cross_domain_section,
        entity_hints_section=entity_hints_section,
        directive_section=directive_section,
        query_blueprint=query_blueprint,
        schema_reference=schema_reference,
        anti_patterns=anti_patterns,
        reasoning_directive=reasoning_directive,
        unresolved_joins_section=unresolved_joins_section,
        feedback_section=feedback_section,
        query_patterns_section=query_patterns_section,
        prior_sql_section=prior_sql_section,
        cte_column_plan=cte_column_plan,
        candidate_join_paths_section=candidate_join_paths_section,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from app.core.retry import retry_async

    llm = get_llm("deep")

    @llm_breaker
    async def _call():
        return await retry_async(
            lambda: llm.ainvoke(prompt, config=config),
            service="bedrock-sql-generator",
            max_attempts=3,
            backoff_base=5.0,
        )

    response = await _call()
    sql = _format_sql(parse_tag(response.content or "", "sql") or "")
    logger.info(
        "sql_generator | SQL generated | thread={} | anchor={} | sql_len={} | pattern_matched={} | pattern={} | reasoning=DEEP",
        state["thread_id"], ir.anchor_tables, len(sql), pattern_matched, pattern_name,
    )
    return sql


async def _plan_cte_columns(
    spec: dict,
    query_blueprint: str,
    schema_reference: str,
    state: AnalyticsState,
    config: RunnableConfig,
    directive_section: str = "",
) -> str:
    """Fast pre-pass: ask a focused model to solve CTE column forwarding before SQL is written.

    Returns the plan string (content inside <plan> tags), or "" on failure.
    """
    has_measures = bool(spec.get("measures"))
    if not has_measures:
        return ""

    if state.get("is_refinement") and not (state.get("recompile_count") or 0):
        return ""

    from app.services.agents.bedrock import get_llm
    from app.core.retry import retry_async
    try:
        llm = get_llm("fast")
        # On recompile, pass the previous error so the planner generates a DIFFERENT plan.
        recompile_count = state.get("recompile_count", 0)
        prior_error = state.get("error") or ""
        if recompile_count > 0 and prior_error:
            prior_error_section = (
                "PREVIOUS PLAN FAILED VALIDATION — generate a different plan that avoids this error:\n"
                f"  {prior_error}\n"
                "If the error is about a SELECT alias forward-reference (e.g. chargeback_ratio used\n"
                "in visa_breach_flag in the same SELECT), add an intermediate CTE to compute the\n"
                "first alias before the second references it."
            )
        else:
            prior_error_section = ""
        # F3: pass anti_patterns + query_patterns so planner avoids known-bad structures
        # _anti_patterns is the pre-formatted string from fetch_anti_patterns (already has header)
        _raw_anti = state.get("_anti_patterns") or ""
        planner_anti_patterns = (
            f"ANTI-PATTERNS (avoid these structural mistakes in your CTE plan):\n{_raw_anti}"
            if isinstance(_raw_anti, str) and _raw_anti and _raw_anti.strip() not in ("(none)", "")
            else ""
        )
        planner_query_patterns = _build_query_patterns_section(
            state.get("_query_patterns") or [],
            state.get("_pattern_matched", False),
            state.get("_pattern_name"),
        )
        # F4: append candidate join paths to query_blueprint so planner sees alternatives
        ir_for_planner = state.get("_planner_ir")
        col_lookup_for_planner = state.get("_planner_col_lookup") or {}
        planner_candidate_section = ""
        if ir_for_planner is not None and ir_for_planner.candidate_join_paths:
            planner_candidate_section = _build_candidate_join_paths_section(ir_for_planner, col_lookup_for_planner)
        planner_blueprint = query_blueprint + ("\n" + planner_candidate_section if planner_candidate_section else "")

        prompt = CTE_COLUMN_PLANNER_PROMPT.format_messages(
            question=state.get("effective_question") or state.get("question", ""),
            directive_section=directive_section,
            prior_error_section=prior_error_section,
            query_blueprint=planner_blueprint,
            schema_reference=schema_reference,
            anti_pattern_section=planner_anti_patterns,
            query_pattern_section=planner_query_patterns,
            reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        )
        response = await retry_async(
            lambda: llm.ainvoke(prompt, config=config),
            service="bedrock-cte-planner",
            max_attempts=2,
            backoff_base=3.0,
        )
        plan = parse_tag(response.content or "", "plan").strip()
        logger.info(
            "sql_generator | CTE planner done | thread={} | plan_len={}",
            state["thread_id"], len(plan),
        )
        return plan
    except Exception as e:
        logger.warning(
            "sql_generator | CTE planner failed (degrading gracefully) | thread={} | error={}",
            state["thread_id"], e,
        )
        return ""


def _is_exact_value(value) -> bool:
    """Return True when value should stay as exact = match (numeric or date-like).

    Anything else (string codes, names, identifiers) should use ILIKE so
    Neo4j-generated casing mismatches don't silently kill results.
    """
    import re
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, list):
        return all(_is_exact_value(v) for v in value)
    s = str(value)
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        pass
    # Date-like: YYYY-MM-DD, MM/DD/YYYY, YYYY/MM/DD
    if re.match(r"^\d{4}-\d{2}-\d{2}|^\d{2}/\d{2}/\d{4}|^\d{4}/\d{2}/\d{2}", s):
        return True
    return False


def _build_entity_hints_section(schema_ctx: dict) -> str:
    """Inject pre-resolved entity → DB code mappings from the entity_value discovery path.

    These values were already matched against schema vocabulary (value_aliases /
    distinct_values). Using them directly prevents the LLM from guessing filter values.
    The DB code is always the left side of any 'CODE -> Human Name' alias entry.
    """
    hints = schema_ctx.get("entity_hints") or []
    if not hints:
        return ""

    lines = [
        "ENTITY VALUE MATCHES (pre-resolved DB codes — use EXACTLY as shown in WHERE clauses):",
        "RULE: Use these values verbatim. Do NOT expand to human-readable names.",
    ]
    for eh in hints[:8]:
        token = eh.get("token", "")
        table_fqn = eh.get("table_fqn", "")
        column = eh.get("column", "")
        matched = eh.get("matched_value", "")
        # Extract DB code: left side of "CODE -> Human Name", or the value itself
        db_code = matched.split(" -> ")[0].strip() if " -> " in matched else matched
        if token and table_fqn and column and db_code:
            lines.append(f"  '{token}' → {table_fqn}.{column} = '{db_code}'  — use: WHERE {column} = '{db_code}'")

    return "\n".join(lines) + "\n" if len(lines) > 2 else ""


def _build_cross_domain_section(semantic_context: dict) -> str:
    """Inject cross-domain CTE blueprint when query spans multiple business domains.

    hub_table_fqn is the conformed dimension (e.g., lpp.company).
    NOT bridge_table_fqn (the bridging table) — these are different BRIDGES_TO properties.
    """
    if not semantic_context.get("is_cross_domain"):
        return ""
    hub = semantic_context.get("cross_domain_hub") or {}
    hub_fqn = hub.get("hub_table_fqn")
    hub_col = hub.get("hub_join_col", "code")

    if hub_fqn:
        return f"""
--- CROSS-DOMAIN QUERY ---
Conformed dimension hub: {hub_fqn}  (join column: {hub_col})

Pattern:
  1. One CTE per domain — each aggregates its fact table, GROUP BY <company_ref_col>
  2. Final SELECT: FROM {hub_fqn} LEFT JOIN each domain CTE ON <domain>.ref_col = {hub_fqn}.{hub_col}
  3. ALL domain CTEs use LEFT JOIN — data is sparse; INNER JOIN silently drops rows
  4. COALESCE(metric, 0) for all domain metrics — handle missing data gracefully
  5. THRESHOLD LITERALS (e.g. "$200M minimum") come from question text ONLY.
     Do NOT join liquidity_policy or any policy table — that table has NULL company_ref values.

"""
    return """
--- CROSS-DOMAIN QUERY (hub not auto-detected) ---
Tables span multiple business domains. Instructions:
  1. Find shared FK-like columns across anchor tables (company_ref, entity_code, counterparty_ref)
  2. Use LEFT JOIN for all cross-domain connections — data is sparse
  3. Use COALESCE(metric, 0) for all domain metrics
  4. Threshold literals come from the question text only, not from any policy table

"""


def _build_cte_plan_section(plan: str) -> str:
    if not plan:
        return ""
    return (
        "---\n\n"
        "CTE CONTRACT (pre-solved — binding on the SQL you write):\n\n"
        "THREE HARD CONSTRAINTS from this contract (Rule 15):\n"
        "  A. NAME LOCK — use the EXACT CTE names below. Do not rename, merge, or add CTEs.\n"
        "  B. EXPORT CONTRACT — each CTE may only SELECT columns listed in its exports block.\n"
        "     Downstream CTEs and FINAL SELECT may ONLY reference those export aliases.\n"
        "     If you need a column downstream, it MUST appear in the upstream exports — if it\n"
        "     is missing, the contract is wrong; note it in reasoning and add a minimal fix.\n"
        "  C. SOURCE CONSTRAINT — a CTE reading from an upstream CTE cannot use schema.table.col\n"
        "     notation. It references the upstream CTE's export aliases only.\n\n"
        + plan
        + "\n\nDeviating from CTE names or referencing unexported columns is a validation failure."
    )


def _get_join_overlap_evidence(
    on_clause: str,
    col_lookup: dict,
    selectivity_lookup: dict | None = None,
    ref_table_lookup: dict | None = None,
    temporal_grain_lookup: dict | None = None,
    dtype_lookup: dict | None = None,
    semantic_type_lookup: dict | None = None,
) -> str:
    """Return value overlap + cardinality/semantic evidence for a single ON clause."""
    import re
    fqn_re = re.compile(
        r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)"
    )
    cols = fqn_re.findall(on_clause)
    if len(cols) < 2:
        return ""
    col_a_name, col_b_name = cols[0][2], cols[1][2]
    if _is_uuid_col(col_a_name) or _is_uuid_col(col_b_name):
        return "⚠ UUID COLUMN — unique per row, will always return 0 rows as a join key"
    col_a = f"{cols[0][0]}.{cols[0][1]}.{col_a_name}"
    col_b = f"{cols[1][0]}.{cols[1][1]}.{col_b_name}"
    vals_a = set(str(v) for v in (col_lookup.get(col_a) or []))
    vals_b = set(str(v) for v in (col_lookup.get(col_b) or []))
    if not vals_a or not vals_b:
        return ""
    overlap = vals_a & vals_b
    if not overlap:
        return f"⚠ NO VALUE OVERLAP ({len(vals_a)} A-side vs {len(vals_b)} B-side vocabulary values — join will return 0 rows)"
    sample = sorted(overlap)[:3]
    evidence = f"✓ {len(overlap)} shared values (e.g. {', '.join(sample)})"

    # Semantic FK confirmation (only when populated)
    if ref_table_lookup:
        ref_a = ref_table_lookup.get(col_a, "")
        ref_b = ref_table_lookup.get(col_b, "")
        join_to_a = f"{cols[1][0]}.{cols[1][1]}"
        join_to_b = f"{cols[0][0]}.{cols[0][1]}"
        if ref_a and ref_a == join_to_a:
            evidence += f"  [semantic ref → {ref_a} ✓]"
        elif ref_b and ref_b == join_to_b:
            evidence += f"  [semantic ref → {ref_b} ✓]"

    # Temporal grain annotation (only when not 'none'/empty)
    if temporal_grain_lookup:
        grain_a = temporal_grain_lookup.get(col_a, "")
        grain_b = temporal_grain_lookup.get(col_b, "")
        if grain_a and grain_a not in ("none", ""):
            evidence += f"  [temporal_grain={grain_a}]"
        elif grain_b and grain_b not in ("none", ""):
            evidence += f"  [temporal_grain={grain_b}]"

    # Low-cardinality join key warning using actual Redshift pg_stats selectivity
    if selectivity_lookup:
        sel_a = selectivity_lookup.get(col_a, "")
        sel_b = selectivity_lookup.get(col_b, "")
        if sel_a == "low" or sel_b == "low":
            table_a_fqn = f"{cols[0][0]}.{cols[0][1]}"
            table_b_fqn = f"{cols[1][0]}.{cols[1][1]}"
            candidates: list[tuple[str, int]] = []
            for fqn, sel in selectivity_lookup.items():
                if sel not in ("medium", "high"):
                    continue
                parts = fqn.rsplit(".", 1)
                if len(parts) != 2:
                    continue
                tbl, cname = parts
                if tbl not in (table_a_fqn, table_b_fqn):
                    continue
                if cname in (col_a_name, col_b_name) or _is_uuid_col(cname):
                    continue
                if dtype_lookup and dtype_lookup.get(fqn, "") == "boolean":
                    continue
                if semantic_type_lookup and semantic_type_lookup.get(fqn, "") in ("flag", "indicator"):
                    continue
                # For high selectivity: only include confirmed semantic references
                if sel == "high":
                    if not (ref_table_lookup and ref_table_lookup.get(fqn, "")):
                        continue
                candidates.append((cname, 0 if sel == "medium" else 1))
            candidates.sort(key=lambda x: x[1])
            top = [c for c, _ in candidates[:3]]
            hint = f" Narrowing candidates: {', '.join(top)}." if top else ""
            evidence += (
                f"\n    -- ⚠ LOW-CARDINALITY JOIN KEY (filter_selectivity=low,"
                f" ≤50 distinct values per Redshift pg_stats) —"
                f" fact-to-fact join risk: every row on side A matches every row on"
                f" side B with the same key value, multiplying rows.{hint}"
                f" See Rule 1 addendum."
            )
    return evidence


def _build_query_blueprint(spec: dict, schema_ctx: dict, col_lookup: dict | None = None) -> str:
    """Build structured QUERY SPECIFICATION text replacing json.dumps(spec)."""
    lines = ["--- QUERY SPECIFICATION ---", ""]

    if col_lookup is None:
        col_lookup = {
            f"{c.get('table_fqn', '')}.{c.get('name', '')}": (
                c.get("filter_values") or c.get("value_vocabulary") or c.get("sample_values") or []
            )
            for c in schema_ctx.get("columns", [])
            if c.get("table_fqn") and c.get("name")
        }

    _cols = schema_ctx.get("columns", [])
    selectivity_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("filter_selectivity", "")
        for c in _cols if c.get("table_fqn") and c.get("name")
    }
    ref_table_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("referenced_table_fqn", "")
        for c in _cols if c.get("table_fqn") and c.get("name")
    }
    temporal_grain_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("temporal_grain", "")
        for c in _cols if c.get("table_fqn") and c.get("name")
    }
    dtype_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("data_type", "")
        for c in _cols if c.get("table_fqn") and c.get("name")
    }
    semantic_type_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("semantic_type", "")
        for c in _cols if c.get("table_fqn") and c.get("name")
    }

    anchor_tables = spec.get("anchor_tables") or []
    lines.append("ANCHOR TABLES (every one must appear in the SQL — do not drop or add):")
    for t in anchor_tables:
        lines.append(f"  {t}")
    lines.append("")

    result_shape = spec.get("result_shape")
    if result_shape and result_shape != "table":
        lines.append(f"RESULT SHAPE: {result_shape}")
        lines.append("")

    time_filter = spec.get("time_filter")
    anchor_tables_set = set(spec.get("anchor_tables") or [])
    if time_filter:
        from app.services.agents.helpers import render_filter_value, apply_stale_fallback
        primary_fqn = time_filter.get("table_fqn", "")
        tf_col   = f"{primary_fqn}.{time_filter.get('column', '')}"
        tf_op    = time_filter.get("operator", ">=")
        tf_val   = time_filter.get("value", "")
        col_name = time_filter.get("column", "")
        grain_unit = spec.get("temporal_grain") or "month"

        # Render correctly for both single-bound (str) and BETWEEN_SQL (list) values
        tf_clause = render_filter_value(tf_op, tf_val)
        lines.append(f"TIME FILTER:\n  {tf_col} {tf_clause}   [primary]")

        # Stale-data fallback: apply same transformation to MAX(col) instead of CURRENT_DATE.
        # apply_stale_fallback returns None when no substitution is possible or meaningful.
        stale_val = apply_stale_fallback(tf_op, tf_val, col_name, primary_fqn)
        if stale_val is not None:
            stale_clause = render_filter_value(tf_op, stale_val)
            lines.append(
                f"  OR {tf_col} {stale_clause}"
                f"   [stale-data-fallback — same window anchored to MAX date instead of CURRENT_DATE]"
            )

        extra = []
        for t in schema_ctx.get("tables", []):
            fqn  = t.get("fqn", "")
            tcol = t.get("time_dimension_col", "")
            if fqn and tcol and t.get("is_time_series") and fqn != primary_fqn and fqn in anchor_tables_set:
                extra.append(f"  {fqn}.{tcol} {tf_clause}")
                stale_extra = apply_stale_fallback(tf_op, tf_val, tcol, fqn)
                if stale_extra is not None:
                    stale_extra_clause = render_filter_value(tf_op, stale_extra)
                    extra.append(f"  OR {fqn}.{tcol} {stale_extra_clause}   [stale-data-fallback]")
        if extra:
            lines.append("  Apply same boundary to ALL other time-series tables in this query:")
            lines.extend(extra)
            lines.append("  (Omitting these causes full-history scans → timeout)")
        lines.append("")
    else:
        snapshot_tables = [
            (t.get("fqn", ""), t.get("time_dimension_col", ""))
            for t in schema_ctx.get("tables", [])
            if t.get("is_time_series") and t.get("time_dimension_col")
               and t.get("fqn") in anchor_tables_set
        ]
        if snapshot_tables:
            lines.append("TIME FILTER: none specified")
            lines.append("  MANDATORY — these tables are daily snapshots.")
            lines.append("  For current/recent data, filter on BOTH conditions (no truncation, no transformation):")
            for fqn, tcol in snapshot_tables:
                lines.append(f"    WHERE {fqn}.{tcol} = CURRENT_DATE")
                lines.append(f"       OR {fqn}.{tcol} = (SELECT MAX({tcol}) FROM {fqn})")
            lines.append("  CURRENT_DATE covers live data. MAX subquery covers stale snapshots.")
            lines.append("  Never apply DATE_TRUNC or DATEADD to either side.")
            lines.append("  Exception: omit the date filter ONLY for full-history queries (trends, all-time totals).")
            lines.append("")

    measures = spec.get("measures") or []
    dimensions = spec.get("dimensions") or []

    if measures:
        lines.append("MEASURES (wrap each in its aggregate):")
        for m in measures:
            agg = m.get("aggregation") or ""
            fqn = m.get("table_fqn", "")
            col = m.get("column_name", "")
            alias = m.get("alias", col)
            agg_label = agg if (agg and agg.upper() not in ("", "NONE")) else "SUM"
            lines.append(f"  {agg_label}({fqn}.{col})              alias: {alias}")
        lines.append("")

        if dimensions:
            temporal_grains = spec.get("temporal_grains") or []
            temporal_grain  = temporal_grains[0] if temporal_grains else spec.get("temporal_grain")
            lines.append("DIMENSIONS (must be in GROUP BY):")
            for d in dimensions:
                fqn = d.get("table_fqn", "")
                col = d.get("column_name", "")
                alias = d.get("alias", col)
                if temporal_grain and alias != col:
                    lines.append(
                        f"  {fqn}.{col}   alias: {alias}"
                        f"   [base CTE: DATE_TRUNC('{temporal_grain}', {col}) AS {alias}"
                        f" — format for clean display per grain]"
                    )
                else:
                    lines.append(f"  {fqn}.{col}          alias: {alias}")
            lines.append("")
            # Multi-grain instruction: when user asked for two time horizons
            if len(temporal_grains) > 1:
                grains_str = " → ".join(temporal_grains)
                lines.append(
                    f"MULTI-GRAIN OUTPUT ({grains_str}):\n"
                    f"  1. Build base CTE at '{temporal_grains[0]}' grain "
                    f"(DATE_TRUNC('{temporal_grains[0]}', date_col)).\n"
                    f"  2. Build a rollup CTE at '{temporal_grains[1]}' grain by aggregating the base CTE "
                    f"(DATE_TRUNC('{temporal_grains[1]}', period_{temporal_grains[0]})).\n"
                    f"  3. Final SELECT combines both via UNION ALL or joins them side by side."
                )
                lines.append("")
    else:
        lines.append("RESULT TYPE: flat lookup — no GROUP BY")
        lines.append("")

    filters = spec.get("filters") or []
    if filters:
        lines.append("FILTERS:")
        from collections import defaultdict
        fuzzy_groups: dict[tuple, list] = defaultdict(list)   # → [fuzzy — use ~* regex]
        exact_groups: dict[tuple, list] = defaultdict(list)
        other_filters: list[dict] = []
        for f in filters:
            col_key = f"{f.get('table_fqn', '')}.{f.get('column', '')}"
            col_vocab = col_lookup.get(col_key, [])
            is_resolved = f.get("resolved", False)
            if f.get("is_raw_sql"):
                other_filters.append(f)
            elif f.get("operator") in ("ILIKE", "LIKE"):
                fuzzy_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            elif f.get("operator") == "=" and not _is_exact_value(f.get("value")):
                if is_resolved or col_vocab:
                    # Resolved by filter_resolver OR has enum vocabulary → exact match
                    exact_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
                else:
                    # No vocabulary, not resolved → regex fallback
                    fuzzy_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            elif f.get("operator") == "=":
                # Dates and numbers: exact =
                exact_groups[(f.get("table_fqn", ""), f.get("column", ""))].append(f)
            else:
                other_filters.append(f)

        for f in other_filters:
            clause, label = _format_filter_line(f)
            section = "HAVING" if f.get("is_having") else "WHERE"
            lines.append(f"  {section}:   {clause}   {label}")

        # Exact = filters: annotate with data_type so LLM knows quoting rules
        # Numeric/boolean values: NEVER quoted. VARCHAR: always quoted.
        for (tfqn, col), grp in exact_groups.items():
            section = "HAVING" if grp[0].get("is_having") else "WHERE"
            col_key = f"{tfqn}.{col}"
            # Look up data_type from schema context for quoting annotation
            _dtype = ""
            for _c in schema_ctx.get("columns", []):
                if _c.get("table_fqn") == tfqn and _c.get("name") == col:
                    _dtype = _c.get("data_type", "").lower()
                    break
            if any(t in _dtype for t in ("int", "numeric", "decimal", "float", "double", "real")):
                _type_tag = "[numeric — do NOT quote]"
            elif "bool" in _dtype:
                _type_tag = "[boolean — no quotes, write TRUE or FALSE]"
            elif any(t in _dtype for t in ("date", "timestamp", "datetime")):
                _type_tag = "[date/sql expression — do NOT quote sql expressions]"
            else:
                _type_tag = "[varchar — quote required]"

            if len(grp) == 1:
                val = grp[0].get("value", "")
                lines.append(f"  {section}:   {tfqn}.{col} = {_sql_literal(val)}   [exact] {_type_tag}")
            else:
                vals = ", ".join(_sql_literal(g.get("value", "")) for g in grp)
                lines.append(f"  {section}:   {tfqn}.{col} IN ({vals})   [exact — multiple values, use IN] {_type_tag}")

        for (tfqn, col), grp in fuzzy_groups.items():
            section = "HAVING" if grp[0].get("is_having") else "WHERE"
            if len(grp) == 1:
                val = str(grp[0].get("value", ""))
                clause = f"{tfqn}.{col} ~* '{val}'"
                label = "[fuzzy — use ~* regex]"
            else:
                parts = " OR ".join(
                    "{}.{} ~* '{}'".format(tfqn, col, str(g.get("value", "")))
                    for g in grp
                )
                clause = f"({parts})"
                label = "[fuzzy — multiple, use OR ~* regex]"
            lines.append(f"  {section}:   {clause}   {label}")
        lines.append("")

    joins = spec.get("joins") or []
    if joins:
        base_table = joins[0].get("from", anchor_tables[0] if anchor_tables else "")
        lines.append("PRE-COMPUTED JOIN CHAIN (copy this FROM + JOIN sequence verbatim into the first CTE):")
        lines.append(f"  FROM {base_table}")
        for j in joins:
            jtype = j.get("type", "INNER JOIN") or "INNER JOIN"
            to_t = j.get("to", "")
            on_clause = j.get("on", "")
            lines.append(f"  {jtype} {to_t}")
            lines.append(f"    ON {on_clause}")
            evidence = _get_join_overlap_evidence(
                on_clause, col_lookup,
                selectivity_lookup=selectivity_lookup,
                ref_table_lookup=ref_table_lookup,
                temporal_grain_lookup=temporal_grain_lookup,
                dtype_lookup=dtype_lookup,
                semantic_type_lookup=semantic_type_lookup,
            )
            if evidence:
                lines.append(f"    -- {evidence}")
        lines.append("")
    elif anchor_tables:
        lines.append("BASE TABLE (must be in the FROM clause of the first CTE):")
        lines.append(f"  FROM {anchor_tables[0]}")
        lines.append("")

    # E2: Render temporal grains before the sort/limit block
    temporal_grains_list = spec.get("temporal_grains") or []
    if len(temporal_grains_list) >= 2:
        lines.append("TEMPORAL GRAINS (dual-horizon — produce one aggregation CTE per grain, UNION ALL):")
        for g in temporal_grains_list:
            lines.append(f"  {g}  → DATE_TRUNC('{g}', <time_col>)::DATE")
        lines.append("  horizon label: CASE WHEN period <= DATEADD(day, <N>, max_date) THEN '<fine>_view' ELSE '<coarse>_view' END")
        lines.append("  max_date = SELECT MAX(<date_col>) FROM <table>  ← use for the horizon boundary, NOT CURRENT_DATE")
        lines.append("  (Date range WHERE filters still use CURRENT_DATE OR MAX(col) fallback per Rule 2b — max_date here is ONLY for the CASE WHEN label boundary)")
        lines.append("")
    elif temporal_grains_list:
        lines.append(f"TEMPORAL GRAIN: {temporal_grains_list[0]}  (use DATE_TRUNC('{temporal_grains_list[0]}', <time_col>) for bucketing)")
        lines.append("")

    # A1: Render derived_measures and threshold_specs
    derived_measures = spec.get("derived_measures") or []
    if derived_measures:
        lines.append("DERIVED MEASURES (compute in a CTE, reference by alias downstream):")
        for dm in derived_measures:
            alias = dm.get("alias", "")
            expr = dm.get("expression", "")
            agg = dm.get("aggregation", "NONE")
            agg_note = f"  [{agg} aggregation]" if agg and agg.upper() not in ("NONE", "") else ""
            lines.append(f"  {alias} = {expr}{agg_note}")
        lines.append("")

    threshold_specs = spec.get("threshold_specs") or []
    if threshold_specs:
        lines.append("THRESHOLD FLAGS (CASE WHEN in a post-aggregate CTE):")
        for ts in threshold_specs:
            expr = ts.get("expression", "")
            op = ts.get("operator", "<")
            val = ts.get("value", "")
            label = ts.get("label", "threshold_flag")
            having = "  [HAVING — applies after GROUP BY]" if ts.get("is_having") else ""
            lines.append(f"  {label} = CASE WHEN {expr} {op} {val} THEN TRUE ELSE FALSE END{having}")
        lines.append("")

    # E1: Relabel cte_steps as hints so CTE planner names take precedence
    cte_steps = spec.get("cte_steps") or []
    if cte_steps:
        lines.append("CTE STEP HINTS (informational only — CTE_CONTRACT names from the planner take precedence):")
        lines.append("  " + "  →  ".join(cte_steps))
        lines.append("")

    order_by = spec.get("order_by")
    if order_by:
        lines.append(f"SORT:   {order_by}")
    limit = spec.get("limit")
    if limit:
        lines.append(f"LIMIT:  {limit}")

    lines.append(f"""
--- PERFORMANCE DIRECTIVES ---
Write this query as a senior Redshift DBA. Non-negotiable:
  a. Filter early — push WHERE into CTEs, not the outer SELECT.
  b. Aggregate before joining — one aggregation CTE per table, then join small results.
  c. No SELECT * anywhere — name only columns used downstream.
  d. GROUP BY instead of DISTINCT on large result sets.
  e. Apply LIMIT {limit or 100} on the outer SELECT — never omit it.
  f. Include only tables that the question requires.
  g. Multi-fact CTE drive direction: when 2+ fact tables are pre-aggregated into CTEs and the
     final SELECT has ORDER BY + LIMIT, drive from the most-filtered fact CTE — NOT from the
     dimension table:
       ✓ FROM <fact_cte> AS f INNER JOIN <dimension> AS d ON f.<key> = d.<key>
       ✗ FROM <dimension> AS d LEFT JOIN <fact_cte> AS f ON f.<key> = d.<key>
     The second form forces Redshift to read ALL dimension rows before ORDER BY/LIMIT can reduce
     them. Dimension tables (instruments, companies, counterparties) can have millions of rows.
     Fact CTEs are already date-filtered and grouped — they are tiny. Use the fact CTE as the
     driver. Use INNER JOIN (not LEFT JOIN): dimension rows with no matching fact rows are
     semantically out of scope for the requested period.
  h. MAX-date snapshot pattern — NEVER use a scalar correlated subquery directly in WHERE for
     snapshot filtering. Use a pre-computed 1-row CTE instead. Correlated subqueries in WHERE
     repeat the full scan once per CTE branch, causing timeout on large tables.
       ✗ WRONG (correlated, slow):
           WHERE cb.balance_date = (SELECT MAX(balance_date) FROM lpp.cash_balance)
       ✓ CORRECT (pre-computed, fast):
           WITH cb_max AS (SELECT MAX(balance_date) AS max_d FROM lpp.cash_balance)
           ... JOIN cb_max ON cb.balance_date = cb_max.max_d
     For queries with 3+ snapshot CTEs: pre-compute ALL MAX dates in a single CTE using
     UNION ALL to avoid repeated full scans:
           WITH snapshot_max AS (
             SELECT 'cash'          AS tbl, MAX(balance_date) AS max_d FROM lpp.cash_balance
             UNION ALL
             SELECT 'exposure',          MAX(as_of_date)    FROM lpp.counterparty_exposure
           )
     Then JOIN each data CTE to the relevant row from snapshot_max.

  i. COLUMN ALIAS NAMES — MANDATORY business names:
     NEVER use generic placeholders: dimension_1, dimension_2, measure_1, measure_2, etc.
     Every alias must be a meaningful business name derived from the actual column or the question.
       ✗ WRONG:  total_cash_liquidity AS measure_1, NULL AS measure_2
       ✓ CORRECT: total_cash_liquidity AS total_cash_liquidity, gross_exposure AS gross_exposure
     For UNION ALL across domains: keep domain-specific aliases in each branch. Do NOT
     normalize columns to generic placeholders to make them UNION-compatible.
     If domains have different columns, use NULL AS <meaningful_name> — not NULL AS measure_N.""")

    return "\n".join(lines)


def _is_numeric_value(v) -> bool:
    """True if v is a bare number that must not be quoted in SQL."""
    try:
        float(str(v))
        return True
    except (ValueError, TypeError):
        return False


def _sql_literal(v) -> str:
    """Quote v as a SQL string literal unless it is numeric or already a raw SQL expression."""
    return str(v) if _is_numeric_value(v) else f"'{v}'"


def _format_filter_line(f: dict) -> tuple[str, str]:
    """Return (clause_text, label) for a single non-ILIKE filter."""
    tfqn = f.get("table_fqn", "")
    col = f.get("column", "")
    op = f.get("operator", "=")
    value = f.get("value")
    is_raw_sql = f.get("is_raw_sql", False)

    if is_raw_sql:
        return f"{tfqn}.{col} {op} {value}", ""

    if isinstance(value, list):
        quoted = ", ".join(_sql_literal(v) for v in value)
        return f"{tfqn}.{col} IN ({quoted})", "[exact — multiple values, use IN]"

    return f"{tfqn}.{col} {op} {_sql_literal(value)}", "[exact]"


def _build_schema_reference(schema_ctx: dict) -> str:
    """Build structured SCHEMA REFERENCE text replacing json.dumps(schema_ctx)."""
    tables = schema_ctx.get("tables", [])
    columns = schema_ctx.get("columns", [])
    available_joins = schema_ctx.get("available_joins", [])

    col_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("filter_values") or c.get("sample_values") or []
        for c in columns
        if c.get("table_fqn") and c.get("name")
    }

    lines = ["--- SCHEMA REFERENCE ---", ""]

    lines.append("TABLES (all candidate tables — fact/dimension/bridge role shown):")
    for t in tables:
        fqn = t.get("fqn", "")
        role = t.get("typical_join_role", "") or t.get("table_type", "")
        desc = t.get("description", "")
        grain = t.get("grain", "")
        role_str = f" {role:<12}" if role else ""
        desc_str = f" — {desc}" if desc else ""
        grain_str = f"   grain: {grain}" if grain else ""
        lines.append(f"  {fqn:<40}{role_str}{desc_str}{grain_str}".rstrip())
    lines.append("")

    primary_cols = [c for c in columns if "is_groupable" in c or "is_measurable" in c]
    secondary_cols = [c for c in columns if "is_groupable" not in c and "is_measurable" not in c]

    if primary_cols:
        lines.append("PRIMARY COLUMNS (grouped by table — ONLY use columns listed under each table; do not cross-reference):")
        # Group by table_fqn to prevent cross-table column hallucination
        by_table: dict[str, list[dict]] = {}
        for c in primary_cols:
            fqn = c.get("table_fqn", "")
            by_table.setdefault(fqn, []).append(c)

        _semantic_markers = {
            "code":       "[code — use = or IN, never ILIKE]",
            "identifier": "[identifier — join key only, not a filter]",
            "flag":       "[flag — boolean TRUE/FALSE only]",
        }

        for fqn, cols in by_table.items():
            lines.append(f"  ── {fqn} ──")
            for c in cols:
                name = c.get("name", "")
                dtype = c.get("data_type", c.get("semantic_type", ""))
                semantic_type = c.get("semantic_type", "")
                is_measurable = c.get("is_measurable", False)
                is_groupable = c.get("is_groupable", False)
                desc = c.get("description", "")
                filter_values = c.get("filter_values") or c.get("sample_values") or []

                marker = "[AGG]" if is_measurable else ("[GRP]" if is_groupable else "")
                sem_marker = _semantic_markers.get(semantic_type, "")
                col_ref = f"  {fqn}.{name}"
                line = f"{col_ref:<52} {dtype:<10} {marker:<6}"
                if sem_marker:
                    line += f"  {sem_marker}"
                if desc:
                    line += f'  "{desc}"'
                if filter_values:
                    vals_str = ", ".join(str(v) for v in filter_values[:8])
                    line += f"   [enum: {vals_str}]"
                elif is_measurable:
                    line += "   (numeric measure)"
                lines.append(line)
        lines.append("")

    if secondary_cols:
        lines.append("SECONDARY COLUMNS (other candidate tables — available only as JOIN partners for display columns):")
        _sec_semantic_markers = {
            "code":       "[code — use = or IN, never ILIKE]",
            "identifier": "[identifier — join key only, not a filter]",
            "flag":       "[flag — boolean TRUE/FALSE only]",
        }
        for c in secondary_cols:
            fqn = c.get("table_fqn", "")
            name = c.get("name", "")
            dtype = c.get("data_type", c.get("semantic_type", ""))
            semantic_type = c.get("semantic_type", "")
            filter_values = c.get("filter_values") or c.get("sample_values") or []
            sem_marker = _sec_semantic_markers.get(semantic_type, "")
            line = f"  {fqn}.{name:<50} {dtype}"
            if sem_marker:
                line += f"  {sem_marker}"
            if filter_values:
                vals_str = ", ".join(str(v) for v in filter_values[:8])
                line += f"   [enum: {vals_str}]"
            lines.append(line)
        lines.append("")

    if available_joins:
        lines.append("ADDITIONAL JOINS (if you need a table not in PRE-COMPUTED JOINS):")
        for j in available_joins:
            from_t = j.get("from", "")
            to_t = j.get("to", "")
            join_type = j.get("join_type", "JOIN")
            clauses = j.get("join_clauses") or []
            path_tables = j.get("path_tables") or []
            is_multihop = j.get("is_multihop", False)
            hop_count = j.get("hop_count", 1)
            if is_multihop and len(path_tables) >= 3:
                # Multi-hop: show each intermediate table explicitly so LLM writes the full chain
                lines.append(f"  {from_t} →({hop_count}-hop)→ {to_t} (via {', '.join(path_tables[1:-1])})")
                for idx, clause in enumerate(clauses):
                    # Pair clause to the table it joins: path_tables[idx] → path_tables[idx+1]
                    join_target = path_tables[idx + 1] if idx + 1 < len(path_tables) else to_t
                    lines.append(f"    {join_type} {join_target} ON {clause}")
            else:
                lines.append(f"  {from_t} → {to_t}")
                for clause in clauses:
                    lines.append(f"    {join_type} {to_t} ON {clause}")
                    evidence = _get_join_overlap_evidence(clause, col_lookup)
                    if evidence:
                        lines.append(f"    -- {evidence}")

    return "\n".join(lines)


def _find_vocabulary_join_hints(
    unresolved_pairs: list[dict],
    col_lookup: dict,
) -> dict[tuple, list]:
    """For each unresolved (from_fqn, to_fqn) pair, find column pairs with vocabulary overlap.

    Skips UUID columns. Returns up to 3 candidates per pair sorted by overlap count (descending).
    col_lookup: {schema.table.col: [values]}
    """
    results: dict[tuple, list] = {}
    for pair in unresolved_pairs:
        from_fqn = pair.get("from", "")
        to_fqn = pair.get("to", "")
        if not from_fqn or not to_fqn:
            continue
        from_cols = {
            k: set(str(v) for v in vals)
            for k, vals in col_lookup.items()
            if k.startswith(from_fqn + ".") and vals and not _is_uuid_col(k.split(".")[-1])
        }
        to_cols = {
            k: set(str(v) for v in vals)
            for k, vals in col_lookup.items()
            if k.startswith(to_fqn + ".") and vals and not _is_uuid_col(k.split(".")[-1])
        }
        candidates = []
        for fc, fset in from_cols.items():
            for tc, tset in to_cols.items():
                shared = sorted(fset & tset)
                if shared:
                    candidates.append((fc, tc, shared))
        candidates.sort(key=lambda x: -len(x[2]))
        results[(from_fqn, to_fqn)] = [(fc, tc, vals[:5]) for fc, tc, vals in candidates[:3]]
    return results


def _build_unresolved_joins_section(unresolved_pairs: list[dict], col_lookup: dict | None = None) -> str:
    if not unresolved_pairs:
        return ""
    vocab_hints = _find_vocabulary_join_hints(unresolved_pairs, col_lookup or {})
    lines = ["UNRESOLVED JOIN PAIRS — no pre-computed path found in Neo4j. You MUST resolve each of these:\n"]
    for pair in unresolved_pairs:
        from_t = pair.get("from", "")
        to_t = pair.get("to", "")
        candidates = pair.get("candidate_join_columns", [])
        sem_bridge = pair.get("semantic_bridge_columns", [])
        lines.append(f"  {from_t} → {to_t}")
        if candidates:
            lines.append(f"    candidate_join_columns: {candidates}")
            lines.append("    → Check ADDITIONAL JOINS in SCHEMA REFERENCE first (use ON clause exactly if found).")
            lines.append("    → Otherwise JOIN ON the most semantically specific candidate column.")
        elif sem_bridge:
            bridge_strs = [
                f"{from_t}.{b.get('from_col')} = {to_t}.{b.get('to_col')}"
                for b in sem_bridge[:2]
                if b.get("from_col") and b.get("to_col")
            ]
            lines.append(f"    semantic_bridge (similarity >= 0.88): {bridge_strs}")
            lines.append("    → Use the semantic bridge as the ON clause — these columns are semantically equivalent.")
        hints = vocab_hints.get((from_t, to_t), [])
        if hints:
            lines.append("    VOCABULARY OVERLAP HINTS (columns sharing actual data values — strong join candidates):")
            for fc, tc, shared in hints:
                total_note = f"{len(shared)} shown" if len(shared) == 5 else f"{len(shared)} total"
                lines.append(f"      {fc} = {tc}")
                lines.append(f"        shared ({total_note}): {', '.join(shared)}")
        else:
            lines.append("    (no vocabulary overlap found — use column name and description similarity)")
        lines.append("    → NEVER produce a CROSS JOIN or omit the table.\n")
    return "\n".join(lines)


def _build_candidate_join_paths_section(ir: SemanticIR, col_lookup: dict | None = None) -> str:
    """Format all collected join path alternatives for the SQL LLM prompt.

    col_lookup: maps "schema.table.col" → list[str] of sampled values.
    When provided, each clause is annotated with value overlap evidence.
    """
    paths = ir.candidate_join_paths
    if not paths:
        return ""

    _col_lookup = col_lookup or {}

    # Group by (from_fqn, to_fqn) pair
    pairs: dict[tuple[str, str], list[dict]] = {}
    for p in paths:
        key = (p.get("from_fqn", ""), p.get("to_fqn", ""))
        pairs.setdefault(key, []).append(p)

    if not pairs:
        return ""

    lines = ["CANDIDATE JOIN PATHS (primary path pre-selected in PRE-COMPUTED JOIN CHAIN; override only when semantically required):"]
    import re as _re_local
    _fqn_col_re = _re_local.compile(
        r"[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\.([a-zA-Z_][a-zA-Z0-9_$]*)"
    )

    def _clause_has_uuid(clause: str) -> bool:
        return any(_is_uuid_col(m.group(1)) for m in _fqn_col_re.finditer(clause))

    for (from_fqn, to_fqn), path_list in pairs.items():
        lines.append(f"  {from_fqn} → {to_fqn}:")
        for p in path_list:
            tier = p.get("tier", "unknown")
            direction = p.get("direction", "forward")
            hop_count = p.get("hop_count", "?")
            path_tables = p.get("path_tables") or []
            raw_clauses = p.get("join_clauses") or []
            # Strip UUID join clauses — they are unique per row and never produce matches
            clauses = [c for c in raw_clauses if not _clause_has_uuid(c)]
            if not clauses and raw_clauses:
                continue  # all clauses were UUID — skip this path entirely
            intermediate = [t for t in path_tables if t not in (from_fqn, to_fqn)]
            hops_label = f"{hop_count} hop{'s' if hop_count != 1 else ''}"
            via_label = f" via {', '.join(intermediate)}" if intermediate else ""
            dir_label = f" [{direction}]" if direction != "forward" else ""
            if clauses:
                clause_str = "  AND  ".join(clauses)
                lines.append(f"    [{tier}, {hops_label}{via_label}{dir_label}]  {clause_str}")
                for clause in clauses:
                    evidence = _get_join_overlap_evidence(clause, _col_lookup)
                    if evidence:
                        lines.append(f"      -- {evidence}")
            else:
                lines.append(f"    [{tier}, {hops_label}{via_label}{dir_label}]  (no clause)")
    lines.append("")
    lines.append("Use a longer path only when the question semantically requires going through intermediate tables.")
    lines.append("hop_count and intermediate tables indicate which path covers the full relationship.")
    lines.append("Prefer paths with ✓ overlap evidence over paths with no comment or ⚠ warning.")
    lines.append("")
    return "\n".join(lines)


def _build_low_confidence_section(state: AnalyticsState) -> str:
    lcf = state.get("low_confidence_filters") or []
    if not lcf:
        return ""
    lines = ["\nSUSPECT FILTER VALUES (low-confidence resolutions — check these first on 0-row results):"]
    for f in lcf:
        lines.append(
            f"  {f.get('column', '')} resolved '{f.get('raw_value', '')}' "
            f"→ '{f.get('resolved_value', '')}' (fuzzy match)"
        )
    return "\n".join(lines)


def _build_feedback_section(state: AnalyticsState) -> str:
    fb = state.get("feedback_context") or ""
    if not fb:
        return ""
    return (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n"
        f"<feedback_context>{fb}</feedback_context>"
    )


def _build_query_patterns_section(
    query_patterns: list,
    pattern_matched: bool = False,
    pattern_name: str | None = None,
) -> str:
    if not query_patterns:
        return ""
    top = query_patterns[0]
    question_text = top.get("question_text", "")
    intent = top.get("intent", "")
    tables = top.get("tables_used", "")
    outline = top.get("sql_cte_outline", "")
    join_outline = top.get("join_outline", "")
    filter_summary = top.get("filter_summary", "")
    complexity = top.get("complexity", "")
    recompile = top.get("recompile_count") or 0
    repair = top.get("repair_count") or 0
    if not (outline or join_outline):
        return ""
    if pattern_matched and pattern_name:
        header = (
            f"MATCHED PATTERN: \"{pattern_name}\" — adapt this pattern to the current question "
            "rather than generating from scratch:"
        )
    else:
        header = "SIMILAR QUERY PATTERNS (prior successful query for a similar question — use as structural guide):"
    lines = [header]
    if question_text:
        lines.append(f"  Question:   \"{question_text}\"")
    lines += [
        f"  Intent:     {intent}",
        f"  Tables:     {tables}",
    ]
    if complexity:
        lines.append(f"  Complexity: {complexity}")
    if outline:
        lines.append(f"  Structure:  {outline}")
    if join_outline:
        lines.append(f"  Joins:      {join_outline}")
    if filter_summary:
        lines.append(f"  Filters:    {filter_summary}")
    if recompile or repair:
        lines.append(
            f"  Note: this pattern needed {recompile} recompile(s) and {repair} repair(s) — "
            "study its join keys and filter patterns carefully before adapting."
        )
    return "\n".join(lines)


def _build_prior_sql_section(state: AnalyticsState) -> str:
    prior_sql = state.get("prior_sql") or ""
    recompile_count = state.get("recompile_count", 0)

    if not prior_sql:
        return ""

    if recompile_count > 0:
        error = state.get("error") or ""
        error_line = f"Validation error that must be fixed:\n  {error}\n\n" if error else ""
        attempt_num = recompile_count + 1
        return (
            f"PREVIOUS SQL (ATTEMPT {recompile_count} FAILED — this is attempt {attempt_num}):\n\n"
            f"{error_line}"
            f"<prior_sql>{prior_sql}</prior_sql>\n\n"
            "Fix the specific validation error above. Do not repeat the structural mistake that caused it."
        )

    if state.get("execution_error"):
        return ""  # intent_resolver already provides execution error context

    if state.get("is_refinement"):
        return (
            "REFINEMENT CONTEXT — do NOT build a new query from scratch:\n\n"
            f"<prior_sql>\n{prior_sql}\n</prior_sql>\n\n"
            "INSTRUCTIONS:\n"
            "1. Start from the prior SQL above — copy its SELECT columns, CTEs, FROM/JOINs, and WHERE conditions.\n"
            "2. Apply ONLY the specific change the user requests.\n"
            "3. The output MUST be a SELECT statement. "
            "'add' or 'include' means adding a WHERE filter or JOIN — NEVER an INSERT, UPDATE, or DELETE.\n"
            "4. Keep existing SELECT columns, CTEs, and JOINs unless explicitly asked to change them.\n"
            "5. If the QUERY SPECIFICATION below has no measures or dimensions, "
            "copy them from the prior SQL — no changes requested.\n"
        )

    return ""


def _format_sql(sql: str) -> str:
    return format_sql(sql)


def parse_decomposition(raw: str, thread_id: str) -> dict | None:
    from json_repair import loads as json_loads
    output = parse_tag(raw, "output")
    if not output:
        return None
    try:
        return json_loads(output)
    except Exception as e:
        logger.warning("sql_generator | decompose parse failed | thread={} | error={}", thread_id, e)
        return None

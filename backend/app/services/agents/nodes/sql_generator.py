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
from app.services.agents.helpers import build_mission_context, format_sql, parse_tag
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

    # Also cache anti-patterns and query patterns — same Neo4j data on every retry.
    # Use TypedDict-registered keys so LangGraph persists them across node invocations.
    if _cached and state.get("_cached_anti_patterns") is not None:
        anti_patterns = state["_cached_anti_patterns"]
        query_patterns = state.get("_cached_query_patterns") or []
        pattern_matched = state.get("_pattern_matched", False)
        pattern_name = state.get("_pattern_name")
    else:
        anti_patterns = await fetch_anti_patterns(state)
        query_patterns, pattern_matched, pattern_name = await fetch_query_patterns(state)
        state["_cached_anti_patterns"] = anti_patterns
        state["_cached_query_patterns"] = query_patterns
        state["pattern_matched"] = pattern_matched
        state["pattern_name"] = pattern_name

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

    cte_column_plan = ""

    # M2: TIME_FILTER is emitted exclusively by directive_writer in directive_section.
    # Do NOT inject a second AUTHORITATIVE TIME FILTER COLUMN — two sources with no priority rule
    # causes the CTE planner to pick between them non-deterministically.
    time_col_highlight_section = ""

    # M12: avoid injecting literal "(none)" into anti-patterns section — CTE planner already
    # uses the same guard via planner_anti_patterns. Match that behaviour for SQL_GENERATE_PROMPT.
    _anti_raw = anti_patterns if isinstance(anti_patterns, str) else ""
    sql_anti_patterns = "" if (not _anti_raw or _anti_raw.strip() in ("(none)", "")) else _anti_raw

    from app.services.agents.prompts import SQL_GENERATE_PROMPT
    prompt = SQL_GENERATE_PROMPT.format_messages(
        question=state.get("effective_question") or state.get("question", ""),
        cross_domain_section=cross_domain_section,
        entity_hints_section=entity_hints_section,
        directive_section=directive_section,
        time_col_highlight_section=time_col_highlight_section,
        query_blueprint=query_blueprint,
        schema_reference=schema_reference,
        anti_patterns=sql_anti_patterns,
        reasoning_directive=reasoning_directive,
        unresolved_joins_section=unresolved_joins_section,
        feedback_section=feedback_section,
        query_patterns_section=query_patterns_section,
        prior_sql_section=prior_sql_section,
        cte_column_plan=cte_column_plan,
        candidate_join_paths_section=candidate_join_paths_section,
    )

    _mission = build_mission_context(
        state,
        role="Write Redshift SQL that exactly satisfies all directives and answers the user's question",
        feeds="executor → synthesis → user (the actual answer)",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

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
    raw_content = response.content or ""
    sql = _format_sql(parse_tag(raw_content, "sql") or "")
    if not sql:
        logger.warning(
            "sql_generator | empty_sql | thread={} | raw_response_len={} | raw_tail={}",
            state["thread_id"], len(raw_content), raw_content[-500:] if raw_content else "(empty)",
        )
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
            _col_hint = (
                "\nIf the error says 'column X does not exist in table T': that column may exist in a "
                "DIFFERENT anchor table — check the SCHEMA REFERENCE below for every anchor table's columns "
                "and use the correct table alias. Do NOT guess — only reference columns that appear "
                "in the SCHEMA REFERENCE for their specific table.\n"
            ) if "does not exist" in prior_error else ""
            prior_error_section = (
                "PREVIOUS PLAN FAILED VALIDATION — generate a different plan that avoids this error:\n"
                f"  {prior_error}\n"
                f"{_col_hint}"
                "If the error is about a SELECT alias forward-reference (e.g. chargeback_ratio used\n"
                "in visa_breach_flag in the same SELECT), add an intermediate CTE to compute the\n"
                "first alias before the second references it."
            )
        else:
            prior_error_section = ""
        # F3: pass anti_patterns + query_patterns so planner avoids known-bad structures
        _raw_anti = state.get("_cached_anti_patterns") or ""
        planner_anti_patterns = (
            f"ANTI-PATTERNS (avoid these structural mistakes in your CTE plan):\n{_raw_anti}"
            if isinstance(_raw_anti, str) and _raw_anti and _raw_anti.strip() not in ("(none)", "")
            else ""
        )
        planner_query_patterns = _build_query_patterns_section(
            state.get("_cached_query_patterns") or [],
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

        # L4: inject early-filter CTE blueprint for deep-join queries (join_depth > 2)
        # The NAME-LOCKED structure prescribes entity-first CTE ordering to avoid DS_BCAST_INNER.
        early_filter_spec = state.get("early_filter_spec")
        if early_filter_spec:
            planner_blueprint = _build_early_filter_blueprint(early_filter_spec) + "\n\n" + planner_blueprint

        groupings = (state.get("query_plan") or {}).get("required_groupings") or []
        groupings_hint_section = (
            "REQUIRED GROUPINGS (user explicitly requested — ensure GROUP BY for each):\n"
            + "\n".join(f"  • {g}" for g in groupings)
            if groupings else ""
        )

        prompt = CTE_COLUMN_PLANNER_PROMPT.format_messages(
            question=state.get("effective_question") or state.get("question", ""),
            directive_section=directive_section,
            groupings_hint_section=groupings_hint_section,
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
        # M4 + M23: validate plan before declaring it binding — bad name or dead CTE → no contract
        validated = _validate_cte_plan(plan)
        if validated is None:
            logger.warning(
                "sql_generator | cte_plan_invalid_or_dead_cte | falling back to no-contract | thread={}",
                state["thread_id"],
            )
            return ""
        logger.info(
            "sql_generator | CTE planner done | thread={} | plan_len={}",
            state["thread_id"], len(validated),
        )
        return validated
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
            lines.append(f"  '{token}' -> {table_fqn}.{column} = '{db_code}'  — use: WHERE {column} = '{db_code}'")

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


def _validate_cte_plan(plan: str) -> str | None:
    """Return plan unchanged if valid, None if plan is malformed or contains dead CTEs.

    Checks (pure string, no regex):
    - At least one CTE defined
    - All CTE names start with letter or underscore (no digit-leading names)
    - Every CTE except the last appears at least twice (definition + downstream reference)
      — a CTE found only once is a dead CTE that will never feed the FINAL SELECT

    Returns None → caller falls back to no-contract mode (uses SCHEMA DIRECTIVE only).
    Conservative: a corrupt contract is worse than no contract.
    """
    if not plan or not plan.strip():
        return None

    lines = plan.split("\n")
    cte_names: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CTE "):
            name_part = stripped[4:].split(" ")[0].rstrip(":")
            if not name_part:
                return None
            if not (name_part[0].isalpha() or name_part[0] == "_"):
                logger.warning("sql_generator | cte_plan_invalid_name | name={}", name_part)
                return None
            cte_names.append(name_part)

    if not cte_names:
        return None

    # Dead CTE check: each CTE except the last must appear ≥2 times in plan
    # (once as definition, ≥1 as downstream reference)
    for cte_name in cte_names[:-1]:
        count = 0
        pos = 0
        while True:
            idx = plan.find(cte_name, pos)
            if idx == -1:
                break
            count += 1
            pos = idx + len(cte_name)
        if count < 2:
            logger.warning(
                "sql_generator | dead_cte_detected | name={} | occurrences={} | falling back",
                cte_name, count,
            )
            return None

    return plan


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
    ref_table_lookup: dict | None = None,
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
        return "WARNING: UUID COLUMN — unique per row, will always return 0 rows as a join key"
    col_a = f"{cols[0][0]}.{cols[0][1]}.{col_a_name}"
    col_b = f"{cols[1][0]}.{cols[1][1]}.{col_b_name}"
    vals_a = set(str(v) for v in (col_lookup.get(col_a) or []))
    vals_b = set(str(v) for v in (col_lookup.get(col_b) or []))
    if not vals_a or not vals_b:
        return ""
    overlap = vals_a & vals_b
    if not overlap:
        return f"WARNING: NO VALUE OVERLAP ({len(vals_a)} A-side vs {len(vals_b)} B-side vocabulary values — join will return 0 rows)"
    sample = sorted(overlap)[:3]
    evidence = f"VERIFIED: {len(overlap)} shared values (e.g. {', '.join(sample)})"

    # Semantic FK confirmation (only when populated)
    if ref_table_lookup:
        ref_a = ref_table_lookup.get(col_a, "")
        ref_b = ref_table_lookup.get(col_b, "")
        join_to_a = f"{cols[1][0]}.{cols[1][1]}"
        join_to_b = f"{cols[0][0]}.{cols[0][1]}"
        if ref_a and ref_a == join_to_a:
            evidence += f"  [semantic ref confirmed: {ref_a}]"
        elif ref_b and ref_b == join_to_b:
            evidence += f"  [semantic ref confirmed: {ref_b}]"

    return evidence


def _build_early_filter_blueprint(spec: dict) -> str:
    """Build a NAME-LOCKED CTE performance blueprint for deep-join queries.

    Prescribes the entity-first CTE structure that eliminates DS_BCAST_INNER
    and large-table Seq Scans. Pure string construction — no regex.
    """
    fact_table = spec.get("fact_table", "fact_table")
    entity_tables = spec.get("entity_tables") or []
    entity_base = spec.get("entity_base", "entity")
    fact_base = spec.get("fact_base", "fact")
    time_filter_col = spec.get("time_filter_col", "")
    fact_fk = spec.get("fact_fk_to_first_entity", "")
    entity_join_clauses = spec.get("entity_join_clauses") or []
    entity_filters = spec.get("entity_filters") or []
    join_depth = spec.get("join_depth", 3)

    entity_chain = ", ".join(entity_tables)
    ef_lines = [
        f"PERFORMANCE REQUIREMENT (Redshift — deep join detected, join_depth={join_depth}):",
        "Use this MANDATORY CTE structure. CTE names below are NAME-LOCKED — do not rename or merge:",
        "",
        f"CTE matching_{entity_base}  [FILTER CTE — resolves entity filter, returns small result]",
        f"  reads_from: {entity_chain}",
        f"  where_slot: yes  -- all entity filters go here (NOT in base_data or WHERE)",
    ]
    if entity_filters:
        for f in entity_filters[:3]:
            ef_lines.append(f"  filter: {f.get('table_fqn', '')}.{f.get('column_name', '')} {f.get('operator', '=')} '{f.get('value', f.get('raw_value', ''))}'")
    if entity_join_clauses:
        for j in entity_join_clauses[:3]:
            ef_lines.append(f"  join: {j}")
    fk_col = fact_fk.split("=")[0].strip() if fact_fk else f"{fact_table}.fk"
    ef_lines += [
        f"  exports: {fk_col} AS fk_col",
        "",
        f"CTE {fact_base}_window  [FACT WINDOW — narrows fact table scan]",
        f"  reads_from: {fact_table}",
        f"  where: fact.fk_col IN (SELECT fk_col FROM matching_{entity_base})",
    ]
    if time_filter_col:
        ef_lines.append(f"  AND {time_filter_col} >= DATEADD(DAY, -365, CURRENT_DATE)  -- 365-day pre-filter")
    ef_lines += [
        "  exports: all columns needed by downstream CTEs",
        "",
        f"CTE {fact_base}_max  [BOUNDS — MAX(date) from window, NOT from raw {fact_table}]",
        f"  reads_from: {fact_base}_window",
        "  aggregates: yes",
    ]
    if time_filter_col:
        tf_col_name = time_filter_col.split(".")[-1]
        ef_lines.append(f"  exports: max_d AS MAX({tf_col_name})")
    ef_lines += [
        "",
        "CTE base_data  [FINAL WINDOW — apply exact date range from bounds]",
        f"  reads_from: {fact_base}_window CROSS JOIN {fact_base}_max",
        "  where_slot: yes  -- exact date range filter goes here",
        "  exports: all output columns",
        "",
        "This structure eliminates DS_BCAST_INNER and large-table Seq Scan in Redshift EXPLAIN.",
        "PERFORMANCE REQUIREMENT ends here. CTE names above are NAME-LOCKED.",
    ]
    return "\n".join(ef_lines)


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
    ref_table_lookup = {
        f"{c.get('table_fqn', '')}.{c.get('name', '')}": c.get("referenced_table_fqn", "")
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

        lines.append("")
    else:
        pass

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
            # Multi-grain instruction: when user asked for N time horizons
            if len(temporal_grains) > 1:
                grains_str = " -> ".join(temporal_grains)
                cte_steps = "\n".join(
                    f"  {i+1}. Build CTE at '{g}' grain "
                    f"(DATE_TRUNC('{g}', date_col)), export grain_rank = {i}."
                    for i, g in enumerate(temporal_grains)
                )
                lines.append(
                    f"MULTI-GRAIN OUTPUT ({grains_str}):\n"
                    f"{cte_steps}\n"
                    f"  {len(temporal_grains)+1}. Final SELECT: UNION ALL of all {len(temporal_grains)} grain CTEs, "
                    f"ORDER BY period, grain_rank. All CTEs MUST export identical column aliases."
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
                ref_table_lookup=ref_table_lookup,
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
        lines.append(f"TEMPORAL GRAINS (multi-horizon — produce one aggregation CTE per grain, UNION ALL):")
        for i, g in enumerate(temporal_grains_list):
            lines.append(f"  grain_rank={i}  {g}  -> DATE_TRUNC('{g}', <time_col>)::DATE")
        lines.append("  horizon label: CASE WHEN grain_rank = 0 THEN '<finest>_view' ... END  (use grain_rank for ordering, not hardcoded grain names)")
        lines.append("  max_date = SELECT MAX(<date_col>) FROM <table>  -- use for the horizon boundary, NOT CURRENT_DATE")
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
        lines.append("  " + "  ->  ".join(cte_steps))
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
       CORRECT: FROM <fact_cte> AS f INNER JOIN <dimension> AS d ON f.<key> = d.<key>
       WRONG: FROM <dimension> AS d LEFT JOIN <fact_cte> AS f ON f.<key> = d.<key>
     The second form forces Redshift to read ALL dimension rows before ORDER BY/LIMIT can reduce
     them. Dimension tables (instruments, companies, counterparties) can have millions of rows.
     Fact CTEs are already date-filtered and grouped — they are tiny. Use the fact CTE as the
     driver. Use INNER JOIN (not LEFT JOIN): dimension rows with no matching fact rows are
     semantically out of scope for the requested period.
  h. MAX-date snapshot pattern — NEVER use a scalar correlated subquery directly in WHERE for
     snapshot filtering. Use a pre-computed 1-row CTE instead. Correlated subqueries in WHERE
     repeat the full scan once per CTE branch, causing timeout on large tables.
       WRONG (correlated, slow):
           WHERE cb.balance_date = (SELECT MAX(balance_date) FROM lpp.cash_balance)
       CORRECT (pre-computed, fast):
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
       WRONG: total_cash_liquidity AS measure_1, NULL AS measure_2
       CORRECT: total_cash_liquidity AS total_cash_liquidity, gross_exposure AS gross_exposure
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

    _PRIMARY_SEMANTIC = {"amount", "measure", "percentage", "ratio",
                         "dimension", "code", "flag"}
    primary_cols = [c for c in columns if c.get("semantic_type", "").lower() in _PRIMARY_SEMANTIC]
    secondary_cols = [c for c in columns if c.get("semantic_type", "").lower() not in _PRIMARY_SEMANTIC]

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
                semantic_type = c.get("semantic_type", "").lower()
                desc = c.get("description", "")
                filter_values = c.get("filter_values") or c.get("sample_values") or []

                _AGG_SEMANTIC = {"amount", "measure", "percentage", "ratio"}
                _GRP_SEMANTIC = {"dimension", "code", "flag"}
                is_measurable = semantic_type in _AGG_SEMANTIC
                is_groupable = semantic_type in _GRP_SEMANTIC
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
            desc = c.get("description", "")
            filter_values = c.get("filter_values") or c.get("sample_values") or []
            sem_marker = _sec_semantic_markers.get(semantic_type, "")
            line = f"  {fqn}.{name:<50} {dtype}"
            if sem_marker:
                line += f"  {sem_marker}"
            if desc:
                line += f'  "{desc}"'
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
                lines.append(f"  {from_t} ->({hop_count}-hop)-> {to_t} (via {', '.join(path_tables[1:-1])})")
                for idx, clause in enumerate(clauses):
                    # Pair clause to the table it joins: path_tables[idx] → path_tables[idx+1]
                    join_target = path_tables[idx + 1] if idx + 1 < len(path_tables) else to_t
                    lines.append(f"    {join_type} {join_target} ON {clause}")
            else:
                lines.append(f"  {from_t} -> {to_t}")
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
    lines = ["UNRESOLVED JOIN PAIRS — no confirmed path from directive. Use ADDITIONAL JOINS or column overlap below:\n"]
    for pair in unresolved_pairs:
        from_t = pair.get("from", "")
        to_t = pair.get("to", "")
        candidates = pair.get("candidate_join_columns", [])
        lines.append(f"  {from_t} -> {to_t}  [UNRESOLVED]")
        if candidates:
            lines.append(f"    candidate_join_columns (shared names): {candidates}")
            lines.append("    Check ADDITIONAL JOINS first; otherwise JOIN ON the most specific candidate.")
        hints = vocab_hints.get((from_t, to_t), [])
        if hints:
            lines.append("    VOCABULARY OVERLAP (columns sharing actual values):")
            for fc, tc, shared in hints:
                lines.append(f"      {fc} = {tc}  (shared: {', '.join(shared)})")
        if not candidates and not hints:
            lines.append("    (no candidate columns found — use column description similarity)")
        lines.append("    Do NOT produce a CROSS JOIN or omit this table.\n")
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
        lines.append(f"  {from_fqn} -> {to_fqn}:")
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
    lines.append("Prefer paths with VERIFIED overlap evidence over paths with no comment or WARNING notice.")
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
            f"resolved to '{f.get('resolved_value', '')}' (fuzzy match)"
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
    # Query patterns are LLM-generated SQL from prior successful runs — "successful" means no
    # DB error, NOT that the SQL was optimal or structurally correct. Present as reference only:
    # use for table names and join key hints, never as a structural template to copy.
    # Truncate pattern_name to first sentence (or 80 chars) so a long/discouraging intent
    # string from a prior run doesn't bleed into the prompt and cause the LLM to give up.
    _safe_name = (pattern_name or "").split(".")[0].split("\n")[0][:80].strip()
    if pattern_matched and _safe_name:
        header = (
            f"PRIOR QUERY REFERENCE (LLM-generated, not human-verified): \"{_safe_name}\" — "
            "use ONLY as a hint for which tables and join keys were used in a similar question. "
            "Do NOT copy its CTE structure — follow the CTE CONTRACT above instead."
        )
    else:
        header = (
            "SIMILAR QUERY REFERENCE (LLM-generated, not human-verified — reference only): "
            "use ONLY for table names and join key hints. Do NOT copy its structure."
        )
    lines = [header]
    if question_text:
        lines.append(f"  Question:   \"{question_text}\"")
    lines += [
        f"  Tables:     {tables}",
    ]
    if join_outline:
        lines.append(f"  Join keys:  {join_outline}")
    if filter_summary:
        lines.append(f"  Filters:    {filter_summary}")
    if recompile or repair:
        lines.append(
            f"  Warning: this prior query needed {recompile} recompile(s) and {repair} repair(s) — "
            "its structure had problems; use only its table/join-key hints."
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

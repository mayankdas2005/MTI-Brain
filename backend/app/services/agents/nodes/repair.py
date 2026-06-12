"""LLM-based SQL repair for the executor node.

Called when executor gets a DB error and repair_count < MAX_REPAIR.
Uses Opus to rewrite the broken SQL from error + schema context.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import (
    REASONING_DIRECTIVE_REPAIR,
    REPAIR_PROMPT,
    REPAIR_SYNTAX_PROMPT,
    REPAIR_STRUCTURE_PROMPT,
)

_PERFORMANCE_DIRECTIVE = """--- PERFORMANCE DIRECTIVES ---
If the error is a timeout or the query is slow, rewrite for Redshift performance as a senior DBA.
Business logic, tables, filters, and metric definitions must stay identical. Apply in order:
  PRE-FLIGHT (always apply regardless of error type):
  0a. VALUES: FILTER DIRECTIVE DB codes are authoritative — preserve verbatim.
      Do not substitute human labels ('Active') for DB codes ('OUTSTANDING').
  0b. JOINS: SCHEMA DIRECTIVE JOIN_CHAIN ON clauses must be preserved. Do not substitute
      weak joins (company_ref) when the chain specifies a FK join (facility_ref = code).
  0c. EXECUTE INSTRUCTIONS: COMPUTATION formulas, COMPUTED_FILTER predicates (IS NULL,
      thresholds), BENCHMARK_RATE_FILTER, and any SQL execution instruction in QUERY DIRECTIVE
      must be preserved — they are required for semantic correctness.
  0d. NON-ANCHOR: Remove WHERE/EXISTS on tables tagged [WARNING: non-anchor table] in FILTER
      DIRECTIVE. These are entity_hint injections for unrelated tables.
  0e. TABLES: SCHEMA DIRECTIVE ANCHOR_TABLES is the closed set — no extra joins.
  0f. REDSHIFT DIALECT: INTERVAL with months/years is NOT supported. Fix any occurrence:
      ✗ INTERVAL '1 year' / INTERVAL '3 months' / INTERVAL '4 weeks' / date + INTERVAL '...'
      ✓ DATEADD(year,-1,date) / DATEADD(month,-3,date) / DATEADD(week,4,date)

  i. CONDITIONAL — If the error message mentions a subquery, EXISTS, or IN clause AND those
     tables are not in the ANCHOR TABLES of QUERY INTENT → remove those subqueries as the first
     action before any other performance fix. Otherwise skip Rule i and proceed to Rule c.
     When applicable: these are hallucinations that eliminate rows; removing them is correct.
  c. Drop SELECT * and unused CTE columns.
  d. Replace DISTINCT with GROUP BY on explicit columns.
  e. Apply the LIMIT from the original QUERY SPECIFICATION; add LIMIT 100 if absent.
  f. Flatten CTEs that read the same source into one.
  g. CRITICAL — do NOT drop any JOIN that resolves an entity name filter. JOINs to reference
     tables (e.g., lpp.bank, lpp.counterparty, lpp.currency) translate the user's entity name
     (e.g., 'JPMorgan') into a DB code (e.g., 'BANK_JPM'). Removing them produces wrong data.
     If ENTITY VALUE MATCHES appears in SCHEMA REFERENCE, those columns MUST remain in the JOIN chain.
  h. CRITICAL — do NOT change or invent filter values. The QUERY INTENT section lists the
     RESOLVED filter values — use them verbatim in the rewrite. If QUERY INTENT says
     balance_type = 'CLOSING', keep exactly 'CLOSING'; never substitute 'Closing Balance' or
     any other variant. If QUERY INTENT says branch_ref = 'BR_JPM_NY', keep 'BR_JPM_NY'.
     Never fabricate reference codes that do not appear in QUERY INTENT or SCHEMA REFERENCE."""
from app.services.agents.sql_validator_logic import validate_sql
from app.services.agents.state import AnalyticsState


_SYNTAX_SIGNALS = (
    "interval", "dateadd", "date_add", "boolean", "cast", "union all", "union", "order by",
    "syntax error", "parse error", "unexpected token",
)
_STRUCTURE_SIGNALS = (
    "not exported by upstream", "ambiguous", "42702", "does not exist in table",
    "column reference", "not in scope", "cte", "undefined column",
)


def _classify_error(error_msg: str) -> str:
    lower = error_msg.lower()
    for sig in _STRUCTURE_SIGNALS:
        if sig in lower:
            return "structure"
    for sig in _SYNTAX_SIGNALS:
        if sig in lower:
            return "syntax"
    return "general"


def _extract_root_cost(text: str) -> float:
    """Extract the upper bound cost estimate from the first EXPLAIN cost= line. Pure string split."""
    for line in text.split("\n"):
        if "cost=" in line:
            after_cost = line.split("cost=", 1)[1]
            range_part = after_cost.split(" ")[0]
            upper = range_part.split("..")[-1]
            try:
                return float(upper)
            except ValueError:
                continue
    return 0.0


def _parse_explain_flags(text: str, cost_threshold: float = 10_000_000) -> list[str]:
    """Parse Redshift EXPLAIN output for performance anti-patterns. Pure string search, no regex."""
    flags: list[str] = []
    for line in text.split("\n"):
        # Heavy redistribution — both tables shuffled (worst case, DISTKEY mismatch)
        if "DS_DIST_BOTH" in line and "DIST_BOTH" not in flags:
            flags.append("DIST_BOTH")
        # Broadcast inner — entire inner table sent to every node
        if "DS_BCAST_INNER" in line and "CROSS_JOIN_BROADCAST" not in flags:
            flags.append("CROSS_JOIN_BROADCAST")
        # One-sided outer redistribution
        if "DS_DIST_OUTER" in line and "DIST_OUTER" not in flags:
            flags.append("DIST_OUTER")
        # One-sided inner redistribution (moderate — OK if inner is small)
        if "DS_DIST_INNER" in line and "DIST_INNER" not in flags:
            flags.append("DIST_INNER")
        # All-diststyle redistributions (moderate)
        if "DS_DIST_ALL_INNER" in line and "DIST_ALL_INNER" not in flags:
            flags.append("DIST_ALL_INNER")
        if "DS_DIST_ALL_OUTER" in line and "DIST_ALL_OUTER" not in flags:
            flags.append("DIST_ALL_OUTER")
        # Internal planning anomaly
        if "DS_DIST_ERR" in line and "DIST_ERR" not in flags:
            flags.append("DIST_ERR")
        # Nested loop warning string (appended by Redshift planner)
        if "Nested Loop Join in the query plan" in line and "CARTESIAN_RISK" not in flags:
            flags.append("CARTESIAN_RISK")
        # Nested loop as a plan node type (different EXPLAIN format, same issue)
        if "->  Nested Loop" in line and "NESTED_LOOP" not in flags:
            flags.append("NESTED_LOOP")
        # Disk spill — sort/hash ran out of memory
        if "External Sort" in line and "DISK_SPILL" not in flags:
            flags.append("DISK_SPILL")
        # Large intermediate materialization
        if "->  Materialize" in line and "MATERIALIZE" not in flags:
            flags.append("MATERIALIZE")
    # Large table scan — separate pass since it needs cost extraction
    for line in text.split("\n"):
        if "Seq Scan on" in line and "cost=" in line and "LARGE_TABLE_SCAN" not in flags:
            if _extract_root_cost(line) > cost_threshold:
                flags.append("LARGE_TABLE_SCAN")
    return flags


def _format_sql(sql: str) -> str:
    if not sql.strip():
        return sql
    try:
        import sqlglot
        parsed = sqlglot.parse_one(
            sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE
        )
        if parsed:
            return parsed.sql(dialect="redshift", pretty=True)
    except Exception:
        pass
    return sql.strip()


async def attempt_repair(
    state: AnalyticsState,
    sql_list: list[str],
    ir_list: list[dict],
    errors: list[str],
    repair_count: int,
    config: RunnableConfig,
    schema_context: dict | None = None,
) -> dict | None:
    """Use Opus to repair broken SQL. Returns updated state dict or None."""
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    logger.warning("repair | attempting repair | thread={} | repair_count={}", state["thread_id"], repair_count)

    anti_patterns = "(none)"
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if patterns:
            ap_lines = []
            for p in patterns:
                element = p.get("failing_element")
                line = f"- [{p.get('error_type', 'error')}]"
                if element:
                    line += f" element={element} |"
                line += f" {p.get('error_summary', '')}"
                ap_lines.append(line)
            anti_patterns = "\n".join(ap_lines)
    except Exception:
        pass

    sc = schema_context or {}
    first_sql = sql_list[0] if sql_list else ""
    first_ir = ir_list[0] if ir_list else {}
    error_msg = "; ".join(errors[:3])

    semantic_ir_text = _build_semantic_ir_text(first_ir)
    schema_reference = _build_schema_reference_for_repair(sc, sql=first_sql)
    candidate_paths_section = _build_candidate_paths_section(first_ir)

    invalid_cols = _extract_invalid_columns(error_msg)
    invalid_cols_section = (
        f"\nINVALID COLUMNS — these do NOT exist in Redshift, do not use them under any name:\n"
        + "\n".join(f"  ✗ {c}" for c in invalid_cols)
        if invalid_cols else ""
    )

    # Build repair history context — prevents circular repairs by showing what was tried
    repair_history: list[dict] = list(state.get("repair_history") or [])
    prior_attempts_detail = f"PRIOR REPAIR ATTEMPTS:\nThis is repair attempt {repair_count + 1}."

    if repair_history:
        # Show only the most recent prior attempt — more than 1 entry causes negative
        # anchoring where the LLM produces variations of failed approaches rather than
        # genuinely different ones. Framed as structural observation, not prohibition.
        last = repair_history[-1]
        fp = last.get("sql_fingerprint")
        err = last.get("error", "")
        if fp:
            prior_attempts_detail += (
                f"\nPREVIOUS ATTEMPT STRUCTURE (attempt {last['attempt']}) produced error: '{err}'\n"
                f"  That approach used: tables={fp.get('tables',[])} | "
                f"joins={fp.get('join_ons',[])} | CTEs={fp.get('cte_count',0)} | "
                f"GROUP BY={'yes' if fp.get('has_group_by') else 'no'}\n"
                "  Choose a structurally different approach — different join strategy, "
                "CTE decomposition, or aggregation order."
            )
        elif last.get("sql_fragment"):
            prior_attempts_detail += (
                f"\nPREVIOUS ATTEMPT (attempt {last['attempt']}) produced error: '{err}'\n"
                f"  SQL preview: {last['sql_fragment'][:300]}\n"
                "  Choose a structurally different approach."
            )
        else:
            prior_attempts_detail += f"\nPREVIOUS ATTEMPT (attempt {last['attempt']}) failed: '{err}'"
    elif repair_count > 0 and state.get("execution_error"):
        prior_attempts_detail += (
            f"\nThe PREVIOUS repair attempt produced this NEW error: {state['execution_error']}\n"
            "Do NOT try the same fix again — use a completely different approach."
        )

    if invalid_cols_section:
        prior_attempts_detail += invalid_cols_section

    fb = state.get("feedback_context") or ""
    feedback_section = (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n<feedback_context>{fb}</feedback_context>"
        if fb else ""
    )

    from app.services.agents.helpers import build_directive_section, _build_entity_tokens_section
    directive_section = build_directive_section(state)

    entity_tokens_section = _build_entity_tokens_section(state.get("entity_tokens") or [])

    resolved_intent_repair = state.get("resolved_intent") or {}
    _time_filter_col = resolved_intent_repair.get("time_filter_col") or ""
    time_col_highlight_section = (
        f"AUTHORITATIVE TIME FILTER COLUMN: {_time_filter_col} — use this, do not substitute"
        if _time_filter_col else ""
    )

    _perf_directive = _PERFORMANCE_DIRECTIVE if any(
        kw in e.lower()
        for e in errors
        for kw in ("timeout", "canceling", "statement timeout", "query_timeout")
    ) else ""

    # P1: classify error FIRST — EXPLAIN is skipped for syntax/structure errors where it
    # would fail identically to execution (column/syntax validation runs at plan time).
    # Only "general" (execution) errors — timeouts, cartesian joins, 0 rows — benefit from EXPLAIN.
    error_type = _classify_error(error_msg)

    # Run EXPLAIN on the failing SQL — fires only here (after first execution failure),
    # not pre-emptively in sql_validator on every query.
    _explain_flags: list[str] = []
    if error_type == "general":
        try:
            from app.services.agents.redshift_client import run_explain as _run_explain
            _explain_lines = await _run_explain(first_sql)
            _explain_text = "\n".join(_explain_lines)
            _explain_flags = _parse_explain_flags(_explain_text)
            if _explain_flags:
                logger.info("repair | explain_flags={} | thread={}", _explain_flags, state["thread_id"])
        except Exception as _ex:
            logger.debug("repair | explain failed (non-blocking) | error={}", _ex)

    if _explain_flags:
        _exp_lines = [f"REDSHIFT EXPLAIN FLAGS (from failed query analysis): {', '.join(str(f) for f in _explain_flags)}"]

        # Timeout signature: large table scan + broadcast/nested loop = entity filter not pre-applied
        if any(f in _explain_flags for f in ("CROSS_JOIN_BROADCAST", "CARTESIAN_RISK", "NESTED_LOOP")) \
                and "LARGE_TABLE_SCAN" in _explain_flags:
            _exp_lines.append(
                "  → TIMEOUT PATTERN: large fact table (~hundreds of thousands of rows) scanned "
                "THEN broadcast to every node for a nested loop join. The entity filter was applied "
                "AFTER the full scan. Rewrite: (1) matching_entity CTE resolves entity filters first "
                "(returns ~1-10 rows), (2) fact_window CTE joins ON matching_entity + 365-day date "
                "pre-filter (narrows to hundreds), (3) bounds CTE computes MAX(date) from fact_window, "
                "(4) base_data applies exact date range."
            )
        elif any(f in _explain_flags for f in ("CROSS_JOIN_BROADCAST", "CARTESIAN_RISK", "NESTED_LOOP")):
            _exp_lines.append(
                "  → Near-cartesian join detected. Apply entity filters BEFORE joining the large "
                "fact table — use a filtering CTE (matching_entity) first, then join on the result."
            )

        if any(f in _explain_flags for f in ("DIST_BOTH", "DS_DIST_ALL_NONE")):
            _exp_lines.append(
                "  → DS_DIST_BOTH: join columns are not Redshift DISTKEYs — rows are redistributed "
                "across nodes (expensive but not a data correctness issue). Pre-aggregate or filter "
                "one side of the join to reduce redistributed row count."
            )

        if "DIST_OUTER" in _explain_flags:
            _exp_lines.append(
                "  → DS_DIST_OUTER: outer table redistributed. Pre-filter it to a smaller result "
                "set before the join to reduce shuffled rows."
            )

        if "DISK_SPILL" in _explain_flags:
            _exp_lines.append(
                "  → DISK_SPILL: sort/hash ran out of memory and spilled to disk — very slow. "
                "Add a pre-aggregation CTE to reduce row count before the expensive sort. "
                "Replace SELECT DISTINCT with GROUP BY on explicit columns."
            )

        if "LARGE_TABLE_SCAN" in _explain_flags and not any(
            f in _explain_flags for f in ("CROSS_JOIN_BROADCAST", "CARTESIAN_RISK", "NESTED_LOOP")
        ):
            _exp_lines.append(
                "  → LARGE_TABLE_SCAN: fact table scanned without entity filter pushdown. "
                "Move entity/reference table filters into an early CTE that runs BEFORE the "
                "fact table join."
            )

        if "MATERIALIZE" in _explain_flags:
            _exp_lines.append(
                "  → MATERIALIZE: large intermediate dataset held in memory. "
                "Pre-filter the materialized subquery with date + entity conditions."
            )

        explain_section = "\n".join(_exp_lines)
    else:
        explain_section = ""

    def _build_prompt(attempts_detail: str) -> list:
        if error_type == "syntax":
            return REPAIR_SYNTAX_PROMPT.format_messages(
                error_message=error_msg,
                original_sql=first_sql,
                prior_attempts_detail=attempts_detail,
                schema_reference=schema_reference,
                reasoning_directive=REASONING_DIRECTIVE_REPAIR,
            )
        if error_type == "structure":
            return REPAIR_STRUCTURE_PROMPT.format_messages(
                error_message=error_msg,
                original_sql=first_sql,
                prior_attempts_detail=attempts_detail,
                candidate_paths_section=candidate_paths_section,
                schema_reference=schema_reference,
                semantic_ir_text=semantic_ir_text,
                reasoning_directive=REASONING_DIRECTIVE_REPAIR,
            )
        return REPAIR_PROMPT.format_messages(
            question=state.get("effective_question") or state.get("question", ""),
            entity_tokens_section=entity_tokens_section,
            time_col_highlight_section=time_col_highlight_section,
            semantic_ir_text=semantic_ir_text,
            schema_reference=schema_reference,
            original_sql=first_sql,
            error_message=error_msg,
            prior_attempts_detail=attempts_detail,
            directive_section=directive_section,
            feedback_section=feedback_section,
            performance_directive=_perf_directive,
            explain_section=explain_section,
            anti_patterns=anti_patterns,
            candidate_paths_section=candidate_paths_section,
            reasoning_directive=REASONING_DIRECTIVE_REPAIR,
        )

    llm = get_llm("deep")

    repaired_sql = ""
    val_error = ""
    for _try in range(2):
        attempts_detail = prior_attempts_detail
        if _try == 1:
            attempts_detail += (
                f"\n\nFIRST REPAIR ATTEMPT produced SQL that failed pre-execution static validation:\n"
                f"  Validation error: {val_error}\n"
                f"  Failing SQL preview:\n{repaired_sql[:500]}\n"
                f"Fix the validation error specifically — do NOT repeat the same structural approach."
            )

        prompt = _build_prompt(attempts_detail)

        @llm_breaker
        async def _call(p=prompt):
            from app.core.retry import retry_async
            return await retry_async(lambda: llm.ainvoke(p, config=config), service="bedrock-repair", max_attempts=2, backoff_base=5.0)

        try:
            response = await _call()
        except Exception as e:
            logger.error("repair | LLM failed | thread={} | try={} | error={}", state["thread_id"], _try + 1, e)
            return None

        raw = response.content or ""
        repaired_sql = _format_sql(parse_tag(raw, "sql") or "")
        if not repaired_sql:
            logger.warning("repair | produced no SQL | thread={} | try={}", state["thread_id"], _try + 1)
            return None

        is_valid, val_error = validate_sql(repaired_sql)
        if is_valid:
            break

        logger.warning(
            "repair | repaired SQL failed validation | thread={} | try={} | error={}",
            state["thread_id"], _try + 1, val_error,
        )
        if _try == 1:
            from app.services.agents.nodes.audit import write_anti_pattern
            asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="validation_error"))
            return None

    new_sql_list = [repaired_sql, *sql_list[1:]]
    logger.info("repair | succeeded | thread={}", state["thread_id"])
    from app.services.agents.nodes.audit import write_anti_pattern, write_audit_log
    asyncio.create_task(write_anti_pattern(state, first_sql, first_ir, error_msg, error_type="repair_input"))
    asyncio.create_task(write_audit_log(state, first_sql, 0, "repaired"))

    # Record this attempt in repair_history for future repair iterations
    new_history_entry: dict = {"attempt": repair_count + 1, "error": error_msg[:300]}
    try:
        import sqlglot
        import sqlglot.expressions as _exp
        tree = sqlglot.parse_one(first_sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE)
        if tree:
            new_history_entry["sql_fingerprint"] = {
                "tables": [t.name for t in tree.find_all(_exp.Table)][:10],
                "join_ons": [j.args["on"].sql() for j in tree.find_all(_exp.Join) if j.args.get("on")][:5],
                "agg_types": list({type(a).__name__ for a in tree.find_all(_exp.AggFunc)})[:5],
                "has_group_by": bool(tree.find(_exp.Group)),
                "cte_count": len(list(tree.find_all(_exp.With))),
            }
        else:
            raise ValueError("parse returned None")
    except Exception:
        import re as _re_hist
        new_history_entry["sql_fingerprint"] = None
        new_history_entry["sql_fragment"] = first_sql[:500]

    return {
        "sql_list": new_sql_list,
        "repair_count": repair_count + 1,
        "repair_history": repair_history + [new_history_entry],
        "error": None,
    }


def _build_candidate_paths_section(ir_dict: dict) -> str:
    """Build CANDIDATE JOIN PATHS section from valid paths in the IR.

    Gives the repair LLM pre-validated ON clauses to use instead of guessing
    column names. Only includes paths that have non-empty join_clauses.
    """
    candidate_paths = ir_dict.get("candidate_join_paths") or []
    valid_paths = [p for p in candidate_paths if p.get("join_clauses")]
    if not valid_paths:
        return ""

    lines = ["CANDIDATE JOIN PATHS (use these for replacement ON clauses when the original is invalid):"]
    for p in valid_paths[:8]:
        clauses = " AND ".join(p.get("join_clauses") or [])
        tier = p.get("tier", "?")
        hops = p.get("hop_count", 1)
        from_fqn = p.get("from_fqn", "")
        to_fqn = p.get("to_fqn", "")
        lines.append(f"  [{tier}, {hops} hop] {from_fqn} → {to_fqn} | {clauses}")
    return "\n".join(lines)


def _extract_invalid_columns(error_msg: str) -> list[str]:
    """Parse Redshift/Postgres 'column X does not exist' errors and return the bad column names."""
    import re
    cols = []
    # Redshift: "column bank_account.bank_code does not exist"
    # Postgres: "column \"bank_code\" does not exist"
    for m in re.finditer(r'column\s+"?([^\s"]+)"?\s+does not exist', error_msg, re.IGNORECASE):
        col = m.group(1).strip('"')
        if col and col not in cols:
            cols.append(col)
    return cols


def _build_semantic_ir_text(ir_dict: dict) -> str:
    """Build structured QUERY INTENT text replacing json.dumps(first_ir)."""
    if not ir_dict:
        return "--- QUERY INTENT ---\n\n(no intent available)"

    lines = ["--- QUERY INTENT (preserve this — do not change the semantic meaning) ---", ""]

    intent = ir_dict.get("intent", "")
    complexity = ir_dict.get("complexity", "")
    anchor_tables = ir_dict.get("anchor_tables", [])
    measures = ir_dict.get("measures", [])
    dimensions = ir_dict.get("dimensions", [])
    time_filter = ir_dict.get("time_filter")
    filters = ir_dict.get("filters", [])
    join_clauses = ir_dict.get("join_clauses", [])
    path_tables = ir_dict.get("path_tables", [])

    if intent:
        lines.append(f"Intent:     {intent}")
    if complexity:
        lines.append(f"Complexity: {complexity}")
    if anchor_tables:
        lines.append(f"Tables:     {', '.join(anchor_tables)}")

    if measures:
        measure_strs = []
        for m in measures:
            agg = m.get("aggregation") or "SUM"
            fqn = m.get("table_fqn", "")
            col = m.get("column_name", "")
            alias = m.get("alias", col)
            measure_strs.append(f"{agg}({fqn}.{col}) AS {alias}")
        lines.append(f"Measures:   {', '.join(measure_strs)}")

    if dimensions:
        dim_strs = [f"{d.get('table_fqn', '')}.{d.get('column_name', '')}" for d in dimensions]
        lines.append(f"Dimensions: {', '.join(dim_strs)}")

    if time_filter:
        tf_col = f"{time_filter.get('table_fqn', '')}.{time_filter.get('column_name', time_filter.get('column', ''))}"
        tf_val = time_filter.get("value", "")
        lines.append(f"Time:       {tf_col}  {tf_val}")

    if filters:
        filter_strs = []
        for f in filters:
            col = f"{f.get('table_fqn', '')}.{f.get('column_name', '')}"
            op = f.get("operator", "=")
            val = f.get("value", "")
            filter_strs.append(f"{col} {op} '{val}'  ← DB CODE (use verbatim, do not change)")
        lines.append(f"Filters:    {', '.join(filter_strs)}")

    valid_joins = [c for c in join_clauses if c]
    if valid_joins:
        lines.append(f"Joins:      {', '.join(valid_joins)}")

    return "\n".join(lines)


def _build_schema_reference_for_repair(sc: dict, sql: str = "") -> str:
    """Build structured SCHEMA REFERENCE for repair.

    Tables referenced in the failing SQL are shown in full (all columns).
    All other tables are sampled (5 columns each) to keep the prompt focused.
    The 25-column hard cap is replaced with per-table prioritization.
    """
    tables = sc.get("tables", [])
    columns = sc.get("columns", [])

    lines = ["--- SCHEMA REFERENCE ---", ""]

    entity_hints = sc.get("entity_hints") or []
    if entity_hints:
        lines.append("ENTITY VALUE MATCHES (authoritative — these tokens matched schema vocabulary directly):")
        for eh in entity_hints[:5]:
            lines.append(
                f"  '{eh.get('token')}' → {eh.get('table_fqn')}.{eh.get('column')}"
                f" (matched: {str(eh.get('matched_value', ''))[:80]})"
                " — JOIN to this table and filter on this column"
            )
        lines.append("")

    if tables:
        lines.append("TABLES:")
        for t in tables[:10]:
            lines.append(f"  {t.get('fqn', '')}")
        lines.append("")

    if not columns:
        return "\n".join(lines)

    # Extract short table names from the failing SQL for prioritization
    sql_tables: set[str] = set()
    if sql:
        try:
            import sqlglot
            import sqlglot.expressions as exp
            for stmt in sqlglot.parse(sql, dialect="redshift"):
                for tbl in stmt.find_all(exp.Table):
                    if tbl.name:
                        sql_tables.add(tbl.name.lower())
        except Exception:
            pass

    # Group columns by table_fqn; SQL-referenced tables sort first
    from collections import defaultdict
    by_table: dict[str, list] = defaultdict(list)
    for c in columns:
        fqn = c.get("table_fqn", "")
        if fqn:
            by_table[fqn].append(c)

    lines.append("COLUMNS (tables in failing SQL shown in full; others sampled):")
    for fqn, cols in sorted(
        by_table.items(),
        key=lambda kv: (kv[0].rsplit(".", 1)[-1] not in sql_tables, kv[0]),
    ):
        short = fqn.rsplit(".", 1)[-1]
        in_sql = short in sql_tables
        shown = cols if in_sql else cols[:5]
        for c in shown:
            name = c.get("name", "")
            dtype = c.get("data_type", "")
            lines.append(f"  {fqn}.{name:<45} {dtype}")
        if not in_sql and len(cols) > 5:
            lines.append(f"  ... (+{len(cols) - 5} more columns in {fqn})")

    return "\n".join(lines)


async def _attempt_performance_repair(
    state: AnalyticsState,
    sql_list: list[str],
    config: RunnableConfig,
) -> dict | None:
    """Rewrite SQL for Redshift performance using EXPLAIN flags.

    Returns {"sql_list": [repaired, ...]} on success, None on failure.
    Never increments repair_count — uses a separate performance_repair_attempted gate.
    """
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from app.services.agents.prompts import REPAIR_PERFORMANCE_PROMPT

    first_sql = sql_list[0] if sql_list else ""
    if not first_sql:
        return None

    explain_flags = state.get("explain_flags") or []
    explain_output = state.get("explain_output") or ""
    if not explain_flags:
        return None

    logger.info(
        "repair | performance repair START | flags={} | thread={}",
        explain_flags, state["thread_id"],
    )
    try:
        prompt = REPAIR_PERFORMANCE_PROMPT.format_messages(
            explain_flags=explain_flags,
            explain_output=explain_output[:3000],
            original_sql=first_sql,
        )
        llm = get_llm("default")

        @llm_breaker
        async def _call(p=prompt):
            from app.core.retry import retry_async
            return await retry_async(
                lambda: llm.ainvoke(p, config=config),
                service="bedrock-perf-repair",
                max_attempts=2,
                backoff_base=3.0,
            )

        response = await _call()
        repaired = _format_sql(parse_tag(response.content or "", "sql") or "")
        if not repaired:
            logger.warning("repair | performance repair produced no SQL | thread={}", state["thread_id"])
            return None
        is_valid, _ = validate_sql(repaired)
        if not is_valid:
            logger.warning("repair | performance repair produced invalid SQL | thread={}", state["thread_id"])
            return None
        logger.info("repair | performance repair OK | thread={}", state["thread_id"])
        return {"sql_list": [repaired, *sql_list[1:]]}
    except Exception as e:
        logger.warning("repair | performance repair failed | thread={} | error={}", state["thread_id"], e)
        return None

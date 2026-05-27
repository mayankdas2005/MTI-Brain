"""Node 2: query_compiler — builds SemanticIR and compiles SQL.

For simple/complex: deterministic, no LLM.
For advanced (decomposed): uses Sonnet to split into sub-queries.
Filter resolution happens BEFORE SQL compilation (routes to [F] if needed).
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json
import uuid

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.prompts import DECOMPOSE_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.semantic_ir import ColumnRef, FilterSpec, SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState


async def query_compiler(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("query_compiler START | thread={} | decompose={}", state["thread_id"], state.get("decompose_needed"))

    resolved = state.get("resolved_intent") or {}
    semantic_context = state.get("semantic_context") or {}

    if state.get("decompose_needed"):
        return await _handle_decomposed(state, resolved, semantic_context, config)
    else:
        return await _handle_single(state, resolved, semantic_context, config)


async def _handle_single(state: AnalyticsState, resolved: dict, semantic_context: dict, config: RunnableConfig) -> dict:
    """Build a single SemanticIR and generate SQL via LLM."""
    try:
        ir = _build_semantic_ir(resolved, semantic_context, sub_query_index=None)
    except Exception as e:
        logger.error("query_compiler | IR build failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't map your question to the data model."}

    has_unresolved = any(not f.resolved for f in ir.filters)
    if ir.time_filter and not ir.time_filter.resolved:
        has_unresolved = True

    if has_unresolved:
        logger.info("query_compiler | unresolved filters | thread={} | routing to filter_resolver", state["thread_id"])
        return {
            "semantic_ir_list": [ir.model_dump()],
            "filter_resolution_needed": True,
            "sql_list": [],
        }

    try:
        sql = await _generate_sql_llm(ir, semantic_context, state, config)
        if not sql:
            raise ValueError("LLM returned empty SQL")
        logger.info("query_compiler DONE (single) | thread={} | sql_len={}", state["thread_id"], len(sql))
        return {
            "semantic_ir_list": [ir.model_dump()],
            "sql_list": [sql],
            "filter_resolution_needed": False,
        }
    except Exception as e:
        logger.error("query_compiler | SQL generate failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't generate a valid query."}


async def _handle_decomposed(state: AnalyticsState, resolved: dict, semantic_context: dict, config: RunnableConfig) -> dict:
    """Use Sonnet to decompose into sub-queries, then build an IR for each."""
    # Skip decomposition when the primary anchor is a rollup table — it already has
    # pre-computed aggregated columns (variance_pct, etc.), so one query suffices.
    anchor_tables = resolved.get("anchor_tables") or _extract_anchor_tables(resolved)
    if anchor_tables:
        ctx_tables = {t["fqn"]: t for t in semantic_context.get("tables", []) if t.get("fqn")}
        if ctx_tables.get(anchor_tables[0], {}).get("is_rollup"):
            logger.info(
                "query_compiler | anchor is rollup, skipping decomposition | thread={} | anchor={}",
                state["thread_id"], anchor_tables[0],
            )
            return await _handle_single(state, resolved, semantic_context, config)

    logger.info("query_compiler | decomposing | thread={}", state["thread_id"])

    context_str = json.dumps({
        "templates": semantic_context.get("templates", [])[:3],
        "tables": semantic_context.get("tables", [])[:8],
        "columns": semantic_context.get("columns", [])[:12],
    }, indent=2)

    anti_patterns = await _fetch_anti_patterns(state)
    query_patterns = await _fetch_query_patterns(state)

    from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    semantic_context_state = state.get("semantic_context") or {}
    session_summary = semantic_context_state.get("session_summary") or state.get("summary") or ""
    feedback_context = state.get("feedback_context") or ""

    conversation_section = (
        f"CONVERSATION CONTEXT:\n<conversation_context>{session_summary}</conversation_context>"
        if session_summary else ""
    )
    feedback_section = (
        f"USER PREFERENCES (apply silently):\n<feedback_context>{feedback_context}</feedback_context>"
        if feedback_context else ""
    )

    validation_error = state.get("error")
    validation_error_section = (
        f"\nPREVIOUS VALIDATION FAILURE — the SQL generated from the previous decomposition "
        f"was rejected by the validator. Fix the sub-queries mentioned below:\n"
        f"<validation_error>{validation_error}</validation_error>\n"
        if validation_error else ""
    )

    prompt = DECOMPOSE_PROMPT.format_messages(
        question=state["question"],
        resolved_intent=json.dumps(resolved, indent=2),
        semantic_context=context_str,
        query_patterns=query_patterns,
        anti_patterns=anti_patterns,
        reasoning_directive=reasoning_directive,
        conversation_section=conversation_section,
        feedback_section=feedback_section,
        validation_error_section=validation_error_section,
    )

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    try:
        response = await _call()
    except Exception as e:
        logger.error("query_compiler decompose LLM failed | thread={} | error={}", state["thread_id"], e)
        return await _handle_single(state, resolved, semantic_context, config)

    decomposition = _parse_decomposition(response.content or "", state["thread_id"])
    if not decomposition:
        logger.warning("query_compiler | decomposition parse failed | falling back to single | thread={}", state["thread_id"])
        return await _handle_single(state, resolved, semantic_context, config)

    # Partial recompile: if only some sub-queries failed validation, keep the
    # passing IRs from the previous run and only regenerate the failing ones.
    failed_indices = set(state.get("failed_sql_indices") or [])
    previous_ir_list = state.get("semantic_ir_list") or []
    sub_queries_from_llm = decomposition.get("sub_queries", [])
    is_partial = bool(failed_indices and previous_ir_list and len(failed_indices) < len(previous_ir_list))
    if is_partial:
        logger.info(
            "query_compiler | partial recompile | failed_indices={} | preserving {} passing IR(s)",
            sorted(failed_indices), len(previous_ir_list) - len(failed_indices),
        )

    ir_list = []
    for i, sub_q in enumerate(sub_queries_from_llm):
        # For partial recompile: skip LLM-suggested sub-queries for passing indices
        if is_partial and i not in failed_indices and i < len(previous_ir_list):
            ir_list.append(previous_ir_list[i])
            continue

        sub_resolved = dict(resolved)
        sub_resolved["anchor_tables"] = sub_q.get("anchor_tables", resolved.get("anchor_tables", []))
        sub_resolved["intent"] = sub_q.get("intent", resolved.get("intent", ""))
        sub_resolved["complexity"] = "simple"

        if not _validate_anchor_tables(sub_resolved["anchor_tables"], semantic_context):
            logger.warning("query_compiler | decomposed sub-query {} has invalid tables | thread={}", i, state["thread_id"])
            return await _handle_single(state, resolved, semantic_context, config)

        try:
            ir = _build_semantic_ir(sub_resolved, semantic_context, sub_query_index=i)
            raw_key = sub_q.get("merge_key")
            if isinstance(raw_key, str):
                raw_key = [raw_key]
            ir.merge_key = raw_key
            raw_depends = sub_q.get("depends_on")
            ir.depends_on = raw_depends if isinstance(raw_depends, int) else None
            ir.merge_strategy = decomposition.get("merge_strategy")
            ir_list.append(ir.model_dump())
        except Exception as e:
            logger.warning("query_compiler | sub-IR {} build failed | thread={} | error={}", i, state["thread_id"], e)
            return await _handle_single(state, resolved, semantic_context, config)

    has_unresolved = any(
        not f["resolved"]
        for ir_dict in ir_list
        for f in ir_dict.get("filters", [])
    )

    if has_unresolved:
        return {"semantic_ir_list": ir_list, "filter_resolution_needed": True, "sql_list": []}

    sql_list = []
    for ir_dict in ir_list:
        ir = SemanticIR(**ir_dict)
        try:
            sql = await _generate_sql_llm(ir, semantic_context, state, config)
            sql_list.append(sql or "")
        except Exception as e:
            logger.error("query_compiler | sub-SQL generate failed | thread={} | error={}", state["thread_id"], e)
            sql_list.append("")

    logger.info("query_compiler DONE (decomposed) | thread={} | sub_queries={}", state["thread_id"], len(ir_list))
    return {
        "semantic_ir_list": ir_list,
        "sql_list": sql_list,
        "filter_resolution_needed": False,
    }


def _build_semantic_ir(resolved: dict, semantic_context: dict, sub_query_index: int | None) -> SemanticIR:
    """Build a SemanticIR from resolved intent and semantic context."""
    anchor_tables = list(resolved.get("anchor_tables") or _extract_anchor_tables(resolved))

    # Ensure every table referenced in measures/dimensions is in anchor_tables so that
    # _load_join_paths can resolve the correct ON clauses. This fixes decomposed sub-queries
    # where DECOMPOSE_PROMPT sets anchor_tables=['lpp.company'] but measures still reference
    # lpp.forecast_vs_actual — without this, joins: [] and the SQL generator hallucinates.
    ref_tables = _extract_anchor_tables(resolved)
    missing = [t for t in ref_tables if t not in anchor_tables]
    if missing:
        anchor_tables = anchor_tables + missing
        logger.info(
            "query_compiler | anchor_tables extended | added={} | final={}",
            missing, anchor_tables,
        )

    join_path_ids, join_clauses, path_tables, join_types = _load_join_paths(anchor_tables)

    measures = [ColumnRef(**m) for m in resolved.get("measures", []) if isinstance(m, dict) and "table_fqn" in m]
    dimensions = [ColumnRef(**d) for d in resolved.get("dimensions", []) if isinstance(d, dict) and "table_fqn" in d]
    filters = _build_filter_specs(resolved.get("filters", []), raw_measures=resolved.get("measures", []))
    time_filter = _build_time_filter(resolved.get("timeframe"), anchor_tables, semantic_context)

    template_id = resolved.get("template_id", "")
    cte_steps = _get_cte_steps(template_id, semantic_context)

    ir = SemanticIR(
        template_id=template_id,
        intent=resolved.get("intent", ""),
        complexity=resolved.get("complexity", "simple"),
        anchor_tables=anchor_tables,
        join_path_ids=join_path_ids,
        join_clauses=join_clauses,
        path_tables=path_tables,
        join_types=join_types,
        measures=measures,
        dimensions=dimensions,
        filters=filters,
        time_filter=time_filter,
        temporal_grain=resolved.get("temporal_grain"),
        cte_steps=cte_steps,
        order_by=resolved.get("order_by", []),
        limit=resolved.get("limit"),
        sub_query_index=sub_query_index,
        depends_on=None,
        merge_key=None,
        merge_strategy=None,
    )
    logger.info(
        "query_compiler | ir_built | template={} | anchor_tables={} | time_filter={}.{} | filters={}",
        template_id, anchor_tables,
        time_filter.table_fqn if time_filter else None,
        time_filter.column_name if time_filter else None,
        [(f.column_name, f.operator, f.is_having) for f in filters],
    )
    return ir


def _extract_anchor_tables(resolved: dict) -> list[str]:
    tables = set()
    for m in resolved.get("measures", []):
        if m.get("table_fqn"):
            tables.add(m["table_fqn"])
    for d in resolved.get("dimensions", []):
        if d.get("table_fqn"):
            tables.add(d["table_fqn"])
    for f in resolved.get("filters", []):
        if f.get("table_fqn"):
            tables.add(f["table_fqn"])
    return list(tables)


def _load_join_paths(anchor_tables: list[str]) -> tuple[list, list, list, list]:
    """Load JoinPaths for consecutive table pairs with fallback."""
    if len(anchor_tables) <= 1:
        return [], [], list(anchor_tables), []

    join_path_ids = []
    all_join_clauses = []
    all_path_tables = [anchor_tables[0]]
    join_types = []

    for i in range(len(anchor_tables) - 1):
        from_table = anchor_tables[i]
        to_table = anchor_tables[i + 1]

        jp = neo4j_client.load_best_join_path(from_table, to_table)
        if not jp:
            jp = neo4j_client.load_best_join_path(to_table, from_table)
            if jp:
                logger.info("query_compiler | join path found via reverse | from={} to={}", from_table, to_table)
        if not jp:
            # Try JOINS_TO direct edge as final fallback
            direct = _load_direct_join(from_table, to_table)
            if direct:
                all_join_clauses.append(direct["clause"])
                all_path_tables.append(to_table)
                join_types.append(direct["join_type"])
            else:
                logger.warning("query_compiler | no join_path | from={} to={} | using fallback ON id=id", from_table, to_table)
                all_join_clauses.append("id = id")
                all_path_tables.append(to_table)
                join_types.append("JOIN")
            continue

        logger.info(
            "query_compiler | join_path | from={} to={} | clauses={} | tables={}",
            from_table, to_table, jp.get("join_clauses"), jp.get("path_tables"),
        )

        join_path_ids.append(jp.get("id", ""))
        clauses = jp.get("join_clauses", [])
        path_tbls = jp.get("path_tables", [from_table, to_table])

        for j, clause in enumerate(clauses):
            left_tbl = path_tbls[j] if j < len(path_tbls) else from_table
            right_tbl = path_tbls[j + 1] if j + 1 < len(path_tbls) else to_table
            all_join_clauses.append(_qualify_join_clause(clause, left_tbl, right_tbl))
            join_types.append("JOIN")

        for tbl in path_tbls[1:]:
            if tbl not in all_path_tables:
                all_path_tables.append(tbl)

    return join_path_ids, all_join_clauses, all_path_tables, join_types


def _build_filter_specs(
    raw_filters: list[dict],
    raw_measures: list[dict] | None = None,
) -> list[FilterSpec]:
    _COMPARISON_OPS = {">", ">=", "<", "<=", "!="}

    agg_measure_cols: set[tuple[str, str]] = set()
    for m in (raw_measures or []):
        if isinstance(m, dict) and m.get("aggregation") and m["aggregation"].upper() not in ("", "NONE"):
            agg_measure_cols.add((m.get("table_fqn", ""), m.get("column_name", "")))

    filters = []
    for f in raw_filters:
        if not f.get("table_fqn") or not f.get("column"):
            continue
        raw_op = (f.get("operator") or "=").strip()
        raw_value = f.get("raw_value", "")
        is_comparison = raw_op in _COMPARISON_OPS
        is_having = (f["table_fqn"], f["column"]) in agg_measure_cols
        filters.append(FilterSpec(
            table_fqn=f["table_fqn"],
            column_name=f["column"],
            operator=raw_op,
            value=raw_value,
            raw_user_value=raw_value,
            resolved=is_comparison,
            is_having=is_having,
        ))
    return filters


def _build_time_filter(timeframe: str | None, anchor_tables: list[str], semantic_context: dict) -> FilterSpec | None:
    if not timeframe:
        return None
    table_fqn, date_col = _find_date_column(anchor_tables, semantic_context)
    from app.services.neo4j_analytics.filter_resolver_logic import resolve_tier3_temporal
    result = resolve_tier3_temporal(timeframe)
    if not result:
        return FilterSpec(
            table_fqn=table_fqn,
            column_name=date_col,
            operator="=",
            value=timeframe,
            raw_user_value=timeframe,
            resolved=False,
        )
    return FilterSpec(
        table_fqn=table_fqn,
        column_name=date_col,
        operator=result["operator"],
        value=result["value"],
        raw_user_value=timeframe,
        resolved=True,
        is_raw_sql=result.get("is_raw_sql", False),
    )


def _find_date_column(anchor_tables: list[str], semantic_context: dict) -> tuple[str, str]:
    """Return (table_fqn, column_name) for the best temporal column across anchor tables."""
    columns = semantic_context.get("columns", [])
    _DATE_TYPES = {"date", "timestamp", "datetime"}
    _DATE_SEMANTICS = {"date", "datetime", "timestamp"}

    for table in anchor_tables:
        for col in columns:
            if col.get("table_fqn") != table:
                continue
            name = col["name"]
            if col.get("temporal_grain"):
                logger.info("query_compiler | date_col resolved | table={} | col={} | via=temporal_grain", table, name)
                return table, name
            if col.get("semantic_type", "").lower() in _DATE_SEMANTICS:
                logger.info("query_compiler | date_col resolved | table={} | col={} | via=semantic_type({})", table, name, col.get("semantic_type"))
                return table, name
            if col.get("data_type", "").lower() in _DATE_TYPES:
                logger.info("query_compiler | date_col resolved | table={} | col={} | via=data_type({})", table, name, col.get("data_type"))
                return table, name

    fallback_table = anchor_tables[0] if anchor_tables else ""
    logger.warning("query_compiler | date_col not found in context | anchor_tables={} | falling back to {}.transaction_date", anchor_tables, fallback_table)
    return fallback_table, "transaction_date"


def _get_cte_steps(template_id: str, semantic_context: dict) -> list[str]:
    for tmpl in semantic_context.get("templates", []):
        if tmpl.get("id") == template_id and tmpl.get("cte_steps"):
            steps = tmpl["cte_steps"]
            if isinstance(steps, list):
                return [s.split(":")[0].strip() for s in steps]
            if isinstance(steps, str):
                return [s.split(":")[0].strip() for s in steps.split(",")]
    return []


_COL_FIELDS_SQL = {
    "name", "table_fqn", "data_type", "semantic_type", "default_aggregation",
    "is_measurable", "is_groupable", "filter_selectivity",
    "sample_values", "value_vocabulary", "value_aliases",
}


def _qualify_join_clause(clause: str, left_table: str, right_table: str) -> str:
    """Prefix bare 'col1 = col2' join clauses with FQN table names.

    JoinPath nodes store unqualified column pairs. The LLM needs
    'lpp.t1.col = lpp.t2.col' to generate correct ON clauses.
    If the clause already contains dots it is returned unchanged.
    """
    if not clause or "." in clause:
        return clause
    if "=" not in clause:
        return clause
    left_col, right_col = [x.strip() for x in clause.split("=", 1)]
    return f"{left_table}.{left_col} = {right_table}.{right_col}"


def _load_direct_join(from_fqn: str, to_fqn: str) -> dict | None:
    """Check JOINS_TO edges between two tables and return the best join clause."""
    try:
        direct = neo4j_client.get_direct_joins([from_fqn, to_fqn])
        logger.debug(
            "query_compiler | _load_direct_join | from={} to={} | raw_edges={}",
            from_fqn, to_fqn, direct,
        )
        for dj in direct:
            f, t = dj.get("from_fqn"), dj.get("to_fqn")
            fc, tc = dj.get("from_col"), dj.get("to_col")
            if not (f and t and fc and tc):
                logger.debug(
                    "query_compiler | _load_direct_join | skipping edge missing cols | edge={}",
                    dj,
                )
                continue
            if (f == from_fqn and t == to_fqn) or (f == to_fqn and t == from_fqn):
                clause = f"{f}.{fc} = {t}.{tc}"
                jtype = dj.get("join_type") or "JOIN"
                logger.info(
                    "query_compiler | join via JOINS_TO edge | from={} to={} | clause={}",
                    from_fqn, to_fqn, clause,
                )
                return {"clause": clause, "join_type": jtype}
        logger.warning(
            "query_compiler | _load_direct_join | no matching JOINS_TO edge | from={} to={} | edges_returned={}",
            from_fqn, to_fqn, len(direct),
        )
    except Exception as exc:
        logger.warning(
            "query_compiler | _load_direct_join | exception | from={} to={} | error={}",
            from_fqn, to_fqn, exc,
        )
    return None


def _build_schema_context(ir: SemanticIR, semantic_context: dict) -> dict:
    """Build schema context for the SQL generation LLM call.

    Passes ALL merged tables and columns from semantic_context (the full 7-path
    retrieval result), not just anchor/path tables. The LLM needs the full picture
    to choose the right columns and avoid hallucination.
    Primary join clauses are already in the semantic_spec; this section gives the
    LLM metadata for every candidate table so it can discover additional joins.
    """
    pinned_cols = {(m.table_fqn, m.column_name) for m in ir.measures + ir.dimensions}
    all_ctx_fqns = {t["fqn"] for t in semantic_context.get("tables", []) if t.get("fqn")}

    # All merged tables — full descriptions, grain, join role
    tables = [
        {k: v for k, v in t.items()
         if k in {"fqn", "name", "description", "grain", "table_type",
                  "is_time_series", "typical_join_role", "natural_measures", "natural_dimensions",
                  "is_rollup"}}
        for t in semantic_context.get("tables", [])
    ]

    # All columns for every candidate table; add description for pinned columns
    columns = []
    for c in semantic_context.get("columns", []):
        if not c.get("table_fqn") or not c.get("name"):
            continue
        row = {k: v for k, v in c.items() if k in _COL_FIELDS_SQL}
        if (c.get("table_fqn"), c.get("name")) in pinned_cols:
            row["description"] = c.get("description", "")
        columns.append(row)

    # Available join paths — single batch JOINS_TO edge query (replaces O(n²) JoinPath calls).
    # JOINS_TO edges are the authoritative source for direct join columns (from_col/to_col).
    available_joins: list[dict] = []
    candidate_fqns = list(set(ir.path_tables) | set(ir.anchor_tables) | all_ctx_fqns)
    try:
        direct_joins = neo4j_client.get_direct_joins(candidate_fqns)
        seen_pairs: set[tuple] = set()
        for dj in direct_joins:
            f, t = dj.get("from_fqn"), dj.get("to_fqn")
            fc, tc = dj.get("from_col"), dj.get("to_col")
            if not (f and t and fc and tc):
                continue
            pair = (min(f, t), max(f, t))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            available_joins.append({
                "from": f,
                "to": t,
                "join_clauses": [f"{f}.{fc} = {t}.{tc}"],
                "join_type": dj.get("join_type") or "JOIN",
                "confidence": dj.get("confidence"),
            })
            if len(available_joins) >= 15:
                break
    except Exception:
        pass

    return {
        "tables": tables,
        "columns": columns,
        "available_joins": available_joins,
        "business_terms": semantic_context.get("business_terms", [])[:5],
    }


async def _generate_sql_llm(
    ir: SemanticIR,
    semantic_context: dict,
    state: AnalyticsState,
    config: RunnableConfig,
) -> str:
    """Generate Redshift SQL from SemanticIR via LLM."""
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
            if i + 1 < len(ir.path_tables)
        ],
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
        "order_by": ir.order_by,
        "limit": ir.limit,
    }

    schema_ctx = _build_schema_context(ir, semantic_context)
    anti_patterns = await _fetch_anti_patterns(state)

    from app.services.neo4j_analytics.prompts import SQL_GENERATE_PROMPT, REASONING_DIRECTIVE_NORMAL
    prompt = SQL_GENERATE_PROMPT.format_messages(
        semantic_spec=json.dumps(spec, indent=2),
        schema_context=json.dumps(schema_ctx, indent=2),
        anti_patterns=anti_patterns,
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        limit=ir.limit or state.get("max_rows") or 100,
    )

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    response = await _call()
    sql = parse_tag(response.content or "", "sql")
    logger.info(
        "query_compiler | sql_generated | thread={} | sql_len={} | anchor={}",
        state["thread_id"], len(sql or ""), ir.anchor_tables,
    )
    return (sql or "").strip()


def _validate_anchor_tables(anchor_tables: list[str], semantic_context: dict) -> bool:
    known_tables = {t["fqn"] for t in semantic_context.get("tables", []) if t.get("fqn")}
    if not known_tables:
        return True
    return all(t in known_tables for t in anchor_tables)


def _parse_decomposition(raw: str, thread_id: str) -> dict | None:
    from json_repair import loads as json_loads
    output = parse_tag(raw, "output")
    if not output:
        return None
    try:
        return json_loads(output)
    except Exception as e:
        logger.warning("query_compiler | decompose parse failed | thread={} | error={}", thread_id, e)
        return None


async def _fetch_anti_patterns(state: AnalyticsState) -> str:
    semantic_context = state.get("semantic_context") or {}
    templates = semantic_context.get("templates", [])
    if not templates:
        return "(none)"
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if not patterns:
            return "(none)"
        return "\n".join(f"- {p.get('error_type', '')}: {p.get('error_summary', '')}" for p in patterns)
    except Exception:
        return "(none)"


async def _fetch_query_patterns(state: AnalyticsState) -> str:
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_query_patterns(embedding)
        if not patterns:
            return "(none)"
        return "\n".join(f"- intent: {p.get('intent', '')} | tables: {p.get('tables_used', '')}" for p in patterns)
    except Exception:
        return "(none)"

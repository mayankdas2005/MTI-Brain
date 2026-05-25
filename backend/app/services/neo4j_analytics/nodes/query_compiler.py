"""Node 2: query_compiler — builds SemanticIR and compiles SQL.

For simple/complex: deterministic, no LLM.
For advanced (decomposed): uses Sonnet to split into sub-queries.
Filter resolution happens BEFORE SQL compilation (routes to [F] if needed).
"""

from __future__ import annotations

import json
import uuid

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.prompts import DECOMPOSE_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.semantic_ir import ColumnRef, FilterSpec, SemanticIR
from app.services.neo4j_analytics.sql_compiler import compile_sql
from app.services.neo4j_analytics.state import AnalyticsState


async def query_compiler(state: AnalyticsState, config: dict) -> dict:
    logger.info("query_compiler START | thread={} | decompose={}", state["thread_id"], state.get("decompose_needed"))

    resolved = state.get("resolved_intent") or {}
    semantic_context = state.get("semantic_context") or {}

    if state.get("decompose_needed"):
        return await _handle_decomposed(state, resolved, semantic_context, config)
    else:
        return await _handle_single(state, resolved, semantic_context)


async def _handle_single(state: AnalyticsState, resolved: dict, semantic_context: dict) -> dict:
    """Build a single SemanticIR and optionally compile SQL."""
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
        sql = compile_sql(ir)
        logger.info("query_compiler DONE (single) | thread={} | sql_len={}", state["thread_id"], len(sql))
        return {
            "semantic_ir_list": [ir.model_dump()],
            "sql_list": [sql],
            "filter_resolution_needed": False,
        }
    except Exception as e:
        logger.error("query_compiler | SQL compile failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't generate a valid query."}


async def _handle_decomposed(state: AnalyticsState, resolved: dict, semantic_context: dict, config: dict) -> dict:
    """Use Sonnet to decompose into sub-queries, then build an IR for each."""
    logger.info("query_compiler | decomposing | thread={}", state["thread_id"])

    context_str = json.dumps({
        "templates": semantic_context.get("templates", [])[:3],
        "tables": semantic_context.get("tables", [])[:8],
        "columns": semantic_context.get("columns", [])[:12],
    }, indent=2)

    anti_patterns = _fetch_anti_patterns(state)
    query_patterns = _fetch_query_patterns(state)

    from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    prompt = DECOMPOSE_PROMPT.format_messages(
        question=state["question"],
        resolved_intent=json.dumps(resolved, indent=2),
        semantic_context=context_str,
        query_patterns=query_patterns,
        anti_patterns=anti_patterns,
        reasoning_directive=reasoning_directive,
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
        return await _handle_single(state, resolved, semantic_context)

    decomposition = _parse_decomposition(response.content or "", state["thread_id"])
    if not decomposition:
        logger.warning("query_compiler | decomposition parse failed | falling back to single | thread={}", state["thread_id"])
        return await _handle_single(state, resolved, semantic_context)

    ir_list = []
    for i, sub_q in enumerate(decomposition.get("sub_queries", [])):
        sub_resolved = dict(resolved)
        sub_resolved["anchor_tables"] = sub_q.get("anchor_tables", resolved.get("anchor_tables", []))
        sub_resolved["intent"] = sub_q.get("intent", resolved.get("intent", ""))
        sub_resolved["complexity"] = "simple"

        if not _validate_anchor_tables(sub_resolved["anchor_tables"], semantic_context):
            logger.warning("query_compiler | decomposed sub-query {} has invalid tables | thread={}", i, state["thread_id"])
            return await _handle_single(state, resolved, semantic_context)

        try:
            ir = _build_semantic_ir(sub_resolved, semantic_context, sub_query_index=i)
            ir.merge_key = sub_q.get("merge_key")
            ir.depends_on = sub_q.get("depends_on")
            ir.merge_strategy = decomposition.get("merge_strategy")
            ir_list.append(ir.model_dump())
        except Exception as e:
            logger.warning("query_compiler | sub-IR {} build failed | thread={} | error={}", i, state["thread_id"], e)
            return await _handle_single(state, resolved, semantic_context)

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
            sql_list.append(compile_sql(ir))
        except Exception as e:
            logger.error("query_compiler | sub-SQL compile failed | thread={} | error={}", state["thread_id"], e)
            sql_list.append("")

    logger.info("query_compiler DONE (decomposed) | thread={} | sub_queries={}", state["thread_id"], len(ir_list))
    return {
        "semantic_ir_list": ir_list,
        "sql_list": sql_list,
        "filter_resolution_needed": False,
    }


def _build_semantic_ir(resolved: dict, semantic_context: dict, sub_query_index: int | None) -> SemanticIR:
    """Build a SemanticIR from resolved intent and semantic context."""
    anchor_tables = resolved.get("anchor_tables") or _extract_anchor_tables(resolved)
    join_path_ids, join_clauses, path_tables, join_types = _load_join_paths(anchor_tables)

    measures = [ColumnRef(**m) for m in resolved.get("measures", []) if isinstance(m, dict) and "table_fqn" in m]
    dimensions = [ColumnRef(**d) for d in resolved.get("dimensions", []) if isinstance(d, dict) and "table_fqn" in d]
    filters = _build_filter_specs(resolved.get("filters", []), semantic_context)
    time_filter = _build_time_filter(resolved.get("timeframe"), anchor_tables)

    template_id = resolved.get("template_id", "")
    cte_steps = _get_cte_steps(template_id, semantic_context)

    return SemanticIR(
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

        jp = neo4j_client.load_join_path(from_table, to_table)
        if not jp:
            jp = neo4j_client.load_join_path_yens(from_table, to_table)
        if not jp:
            logger.warning("query_compiler | no join path | from={} to={}", from_table, to_table)
            all_join_clauses.append("id = id")
            all_path_tables.append(to_table)
            join_types.append("JOIN")
            continue

        join_path_ids.append(jp.get("id", ""))
        clauses = jp.get("join_clauses", [])
        path_tbls = jp.get("path_tables", [from_table, to_table])

        for j, clause in enumerate(clauses):
            all_join_clauses.append(clause)
            join_types.append("JOIN")

        for tbl in path_tbls[1:]:
            if tbl not in all_path_tables:
                all_path_tables.append(tbl)

    return join_path_ids, all_join_clauses, all_path_tables, join_types


def _build_filter_specs(raw_filters: list[dict], semantic_context: dict) -> list[FilterSpec]:
    filters = []
    for f in raw_filters:
        if not f.get("table_fqn") or not f.get("column"):
            continue
        filters.append(FilterSpec(
            table_fqn=f["table_fqn"],
            column_name=f["column"],
            operator="=",
            value=f.get("raw_value", ""),
            raw_user_value=f.get("raw_value", ""),
            resolved=False,
        ))
    return filters


def _build_time_filter(timeframe: str | None, anchor_tables: list[str]) -> FilterSpec | None:
    if not timeframe:
        return None
    from app.services.neo4j_analytics.filter_resolver_logic import resolve_tier3_temporal
    result = resolve_tier3_temporal(timeframe)
    if not result:
        return FilterSpec(
            table_fqn=anchor_tables[0] if anchor_tables else "",
            column_name="transaction_date",
            operator="=",
            value=timeframe,
            raw_user_value=timeframe,
            resolved=False,
        )
    return FilterSpec(
        table_fqn=anchor_tables[0] if anchor_tables else "",
        column_name="transaction_date",
        operator=result["operator"],
        value=result["value"],
        raw_user_value=timeframe,
        resolved=True,
    )


def _get_cte_steps(template_id: str, semantic_context: dict) -> list[str]:
    for tmpl in semantic_context.get("templates", []):
        if tmpl.get("id") == template_id and tmpl.get("cte_steps"):
            steps = tmpl["cte_steps"]
            if isinstance(steps, list):
                return [s.split(":")[0].strip() for s in steps]
            if isinstance(steps, str):
                return [s.split(":")[0].strip() for s in steps.split(",")]
    return []


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


def _fetch_anti_patterns(state: AnalyticsState) -> str:
    semantic_context = state.get("semantic_context") or {}
    templates = semantic_context.get("templates", [])
    if not templates:
        return "(none)"
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        import asyncio
        embedding = asyncio.get_event_loop().run_until_complete(_get_embedding(state["question"]))
        patterns = neo4j_client.search_anti_patterns(embedding)
        if not patterns:
            return "(none)"
        return "\n".join(f"- {p.get('error_type', '')}: {p.get('error_summary', '')}" for p in patterns)
    except Exception:
        return "(none)"


def _fetch_query_patterns(state: AnalyticsState) -> str:
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        import asyncio
        embedding = asyncio.get_event_loop().run_until_complete(_get_embedding(state["question"]))
        patterns = neo4j_client.search_query_patterns(embedding)
        if not patterns:
            return "(none)"
        return "\n".join(f"- intent: {p.get('intent', '')} | tables: {p.get('tables_used', '')}" for p in patterns)
    except Exception:
        return "(none)"

"""SQL generation via LLM for the query_compiler node.

Builds the spec dict from a SemanticIR, logs it fully, then calls the
SQL generation LLM and returns the raw SQL string.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.nodes.schema_context import build_schema_context, fetch_anti_patterns, fetch_query_patterns
from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState


async def generate_sql_llm(
    ir: SemanticIR,
    semantic_context: dict,
    state: AnalyticsState,
    config: RunnableConfig,
) -> str:
    schema_ctx_full = build_schema_context(ir, semantic_context)
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

    logger.info(
        "sql_generator | LLM input | thread={} | anchor_tables={} | pre_loaded_joins={} | unresolved_pairs={} | measures={} | dimensions={} | filters={} | time_filter={}",
        state["thread_id"],
        spec["anchor_tables"],
        [(j["from"].rsplit(".", 1)[-1], j["to"].rsplit(".", 1)[-1], j["on"][:40]) for j in spec["joins"]],
        [(p["from"].rsplit(".", 1)[-1], p["to"].rsplit(".", 1)[-1], p.get("candidate_join_columns")) for p in unresolved_pairs],
        [(m["column_name"], m.get("aggregation")) for m in spec["measures"]],
        [d["column_name"] for d in spec["dimensions"]],
        [(f["column"], f["operator"], str(f["value"])[:20]) for f in spec["filters"]],
        (
            f"{spec['time_filter']['column']} {spec['time_filter']['operator']} {str(spec['time_filter']['value'])[:30]}"
            if spec.get("time_filter") else None
        ),
    )

    anti_patterns = await fetch_anti_patterns(state)
    query_patterns, pattern_matched, pattern_name = await fetch_query_patterns(state)

    unresolved_joins_section = _build_unresolved_joins_section(unresolved_pairs)
    feedback_section = _build_feedback_section(state)
    query_patterns_section = _build_query_patterns_section(query_patterns)
    prior_sql_section = _build_prior_sql_section(state)
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    from app.services.neo4j_analytics.prompts import SQL_GENERATE_PROMPT
    prompt = SQL_GENERATE_PROMPT.format_messages(
        semantic_spec=json.dumps(spec, indent=2),
        schema_context=json.dumps(schema_ctx, indent=2),
        anti_patterns=anti_patterns,
        reasoning_directive=reasoning_directive,
        limit=ir.limit or state.get("max_rows") or 100,
        unresolved_joins_section=unresolved_joins_section,
        feedback_section=feedback_section,
        query_patterns_section=query_patterns_section,
        prior_sql_section=prior_sql_section,
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
        "sql_generator | SQL generated | thread={} | anchor={} | sql_len={} | pattern_matched={} | pattern={} | reasoning={}",
        state["thread_id"], ir.anchor_tables, len(sql or ""), pattern_matched, pattern_name,
        "DEEP" if state.get("deep_analysis") else "NORMAL",
    )
    return (sql or "").strip()


def _build_unresolved_joins_section(unresolved_pairs: list[dict]) -> str:
    if not unresolved_pairs:
        return ""
    lines = [
        "⚠️ UNRESOLVED JOINS — no pre-computed path found in Neo4j. You MUST resolve each of these:\n"
    ]
    for pair in unresolved_pairs:
        from_t = pair.get("from", "")
        to_t = pair.get("to", "")
        candidates = pair.get("candidate_join_columns", [])
        lines.append(f"  {from_t} → {to_t}")
        if candidates:
            lines.append(f"    candidate_join_columns: {candidates}")
            lines.append("    → Check available_joins first (use ON clause exactly if found).")
            lines.append("    → Otherwise JOIN ON the most semantically specific candidate column.")
        else:
            lines.append("    → No candidate columns found. Check available_joins in SCHEMA CONTEXT.")
        lines.append("    → NEVER produce a CROSS JOIN or omit the table.\n")
    return "\n".join(lines)


def _build_feedback_section(state: AnalyticsState) -> str:
    fb = state.get("feedback_context") or ""
    if not fb:
        return ""
    return (
        f"USER SQL PREFERENCES (from prior feedback — apply silently):\n"
        f"<feedback_context>{fb}</feedback_context>"
    )


def _build_query_patterns_section(query_patterns: list) -> str:
    if not query_patterns:
        return ""
    top = query_patterns[0]
    intent = top.get("intent", "")
    tables = top.get("tables_used", "")
    outline = top.get("sql_cte_outline", "")
    if not outline:
        return ""
    return (
        "REFERENCE PATTERN (prior successful query for similar intent — use as structural guide):\n"
        f"  Intent: {intent}\n"
        f"  Tables: {tables}\n"
        f"  CTE outline: {outline}"
    )


def _build_prior_sql_section(state: AnalyticsState) -> str:
    if not (state.get("is_retry") and state.get("prior_sql")):
        return ""
    return (
        "PRIOR SQL (this is a retry — prior SQL failed; do NOT repeat this structure):\n"
        f"<prior_sql>{state['prior_sql']}</prior_sql>\n"
        "Generate a structurally different approach."
    )


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

"""Schema context builder for the SQL generation LLM.

Builds the focused schema context (primary vs secondary columns), discovers
join candidates, fetches anti-patterns and query patterns from Neo4j.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client
from app.services.neo4j_analytics.semantic_ir import SemanticIR
from app.services.neo4j_analytics.state import AnalyticsState

# Primary table columns: full metadata (default_aggregation excluded — infer from data_type instead)
_COL_FIELDS_SQL = {
    "name", "table_fqn", "data_type", "semantic_type",
    "is_measurable", "is_groupable", "filter_selectivity",
    "sample_values", "filter_values", "value_vocabulary", "value_aliases",
}
# Secondary (non-anchor) table columns: minimal fields only
_COL_FIELDS_SECONDARY = {"name", "table_fqn", "data_type", "semantic_type"}


def _discover_candidate_join_columns(from_fqn: str, to_fqn: str, semantic_context: dict) -> list[str]:
    """Column names that exist in BOTH tables — join key candidates when load_best_join_path returned None.

    Uses columns already in semantic_context (no extra queries).
    Excludes audit/admin columns that match by name but are never join keys.
    """
    _EXCLUDE = {
        "created_at", "updated_at", "created_by", "updated_by",
        "deleted_at", "status", "is_deleted", "is_active", "version",
    }
    cols = semantic_context.get("columns") or []
    from_cols = {c["name"] for c in cols if c.get("table_fqn") == from_fqn and c.get("name")}
    to_cols = {c["name"] for c in cols if c.get("table_fqn") == to_fqn and c.get("name")}
    candidates = sorted((from_cols & to_cols) - _EXCLUDE)
    logger.info(
        "schema_context | candidate_join_cols | from={} to={} | common={}",
        from_fqn, to_fqn, candidates[:5],
    )
    return candidates[:5]


def build_schema_context(ir: SemanticIR, semantic_context: dict) -> dict:
    """Build schema context for the SQL generation LLM.

    Primary tables (anchor + path tables): full column metadata + descriptions.
    Secondary tables (all other context tables): minimal metadata only.
    This focuses LLM attention on the tables it actually needs to write SQL against.
    """
    primary_fqns = set(ir.anchor_tables) | set(ir.path_tables)
    all_ctx_fqns = {t["fqn"] for t in semantic_context.get("tables", []) if t.get("fqn")}

    tables = [
        {k: v for k, v in t.items()
         if k in {"fqn", "name", "description", "grain", "table_type",
                  "is_time_series", "typical_join_role", "natural_measures", "natural_dimensions",
                  "is_rollup"}}
        for t in semantic_context.get("tables", [])
    ]

    columns = []
    for c in semantic_context.get("columns", []):
        if not c.get("table_fqn") or not c.get("name"):
            continue
        if c["table_fqn"] in primary_fqns:
            row = {k: v for k, v in c.items() if k in _COL_FIELDS_SQL}
            row["description"] = c.get("description", "")
        else:
            row = {k: v for k, v in c.items() if k in _COL_FIELDS_SECONDARY}
        columns.append(row)

    available_joins: list[dict] = []
    candidate_fqns = list(primary_fqns | all_ctx_fqns)
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

    unresolved_pairs = []
    for i in range(len(ir.join_clauses)):
        if not ir.join_clauses[i] and i + 1 < len(ir.path_tables):
            from_fqn = ir.path_tables[i]
            to_fqn = ir.path_tables[i + 1]
            unresolved_pairs.append({
                "from": from_fqn,
                "to": to_fqn,
                "candidate_join_columns": _discover_candidate_join_columns(from_fqn, to_fqn, semantic_context),
            })

    primary_col_count = sum(1 for c in columns if c.get("table_fqn") in primary_fqns)
    logger.info(
        "schema_context | built | primary_tables={} | primary_cols={} | secondary_cols={} | available_joins={} | unresolved_pairs={}",
        list(primary_fqns),
        primary_col_count,
        len(columns) - primary_col_count,
        [(j["from"].rsplit(".", 1)[-1], j["to"].rsplit(".", 1)[-1]) for j in available_joins],
        [(p["from"].rsplit(".", 1)[-1], p["to"].rsplit(".", 1)[-1]) for p in unresolved_pairs],
    )

    return {
        "tables": tables,
        "columns": columns,
        "available_joins": available_joins,
        "business_terms": semantic_context.get("business_terms", [])[:5],
        "_unresolved_pairs": unresolved_pairs,
    }


async def fetch_anti_patterns(state: AnalyticsState) -> str:
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
        if state.get("semantic_context") is not None:
            state["semantic_context"]["anti_patterns"] = patterns
        return "\n".join(f"- {p.get('error_type', '')}: {p.get('error_summary', '')}" for p in patterns)
    except Exception:
        return "(none)"


async def fetch_query_patterns(state: AnalyticsState) -> tuple[str, bool, str | None]:
    try:
        from app.services.neo4j_analytics.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_query_patterns(embedding)
        if not patterns:
            return "(none)", False, None
        if state.get("semantic_context") is not None:
            state["semantic_context"]["query_patterns"] = patterns
        top = patterns[0]
        name = top.get("intent") or top.get("id") or ""
        formatted = "\n".join(f"- intent: {p.get('intent', '')} | tables: {p.get('tables_used', '')}" for p in patterns)
        return formatted, True, name
    except Exception:
        return "(none)", False, None

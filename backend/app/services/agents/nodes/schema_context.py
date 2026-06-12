"""Schema context builder for the SQL generation LLM.

Builds the focused schema context (primary vs secondary columns), discovers
join candidates, fetches anti-patterns and query patterns from Neo4j.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState

# Primary table columns shown to the SQL generation LLM.
# Only filter_values is included for vocabulary — it contains cleaned DB codes only.
# value_aliases, value_vocabulary, sample_values are intentionally excluded:
#   - value_aliases: raw "CODE -> Human Name" strings cause LLM to use the full alias as a filter value
#   - value_vocabulary: may also store "CODE -> Human Name" format; filter_values already has extracted codes
#   - sample_values: actual data samples may include UUIDs or raw alias strings the LLM misuses
_COL_FIELDS_SQL = {
    "name", "table_fqn", "data_type", "semantic_type",
    "filter_values",
    "description",
    "referenced_table_fqn",  # semantic FK target table (empty for non-reference cols)
    "referenced_column",     # semantic FK target column
}

_UUID_SUFFIXES = ("_uuid", "_guid", "_uid")


def _is_uuid_col_name(col_name: str) -> bool:
    n = col_name.lower()
    return n == "uuid" or any(n.endswith(s) for s in _UUID_SUFFIXES)


def _has_uuid_in_join_clause(clause: str) -> bool:
    """True if either side of a join clause references a UUID column."""
    for part in clause.split("="):
        col = part.strip().rsplit(".", 1)[-1]
        if _is_uuid_col_name(col):
            return True
    return False


# Secondary (non-anchor) table columns: name/type plus three fields needed for
# the SQL generator to understand bridging tables — description (what it does),
# referenced_table_fqn (FK target — identifies join keys), filter_values (enum vocab).
_COL_FIELDS_SECONDARY = {
    "name", "table_fqn", "data_type", "semantic_type",
    "description",
    "referenced_table_fqn",
    "filter_values",
}


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
                  "typical_join_role", "natural_measures", "natural_dimensions", "is_rollup"}}
        for t in semantic_context.get("tables", [])
    ]

    # For primary (anchor + path) tables, also pull from _column_lookup to recover columns
    # that were cut by GLOBAL_CAP in the display set. Without this, the SQL generator sees
    # a sparse schema for anchor tables and hallucinates the missing columns.
    col_lookup: dict[tuple, dict] = semantic_context.get("_column_lookup") or {}
    display_covered: set[tuple] = set()

    columns = []
    for c in semantic_context.get("columns", []):
        if not c.get("table_fqn") or not c.get("name"):
            continue
        key = (c["table_fqn"], c["name"])
        display_covered.add(key)
        if c["table_fqn"] in primary_fqns:
            row = {k: v for k, v in c.items() if k in _COL_FIELDS_SQL}
        else:
            row = {k: v for k, v in c.items() if k in _COL_FIELDS_SECONDARY}
        columns.append(row)

    # Supplement primary table columns from _column_lookup (not already in display set)
    for (tfqn, cname), col_meta in col_lookup.items():
        if tfqn not in primary_fqns:
            continue
        if (tfqn, cname) in display_covered:
            continue
        if _is_uuid_col_name(cname):
            continue
        row = {k: v for k, v in col_meta.items() if k in _COL_FIELDS_SQL}
        if row.get("table_fqn") and row.get("name"):
            columns.append(row)
            display_covered.add((tfqn, cname))

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
            if _is_uuid_col_name(fc) or _is_uuid_col_name(tc):
                logger.debug("schema_context | skip UUID join | {}.{} = {}.{}", f, fc, t, tc)
                continue
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

    # Multi-hop JoinPath joins: fills pairs not covered by direct JOINS_TO edges.
    # seen_pairs already populated from get_direct_joins — direct joins always take priority.
    try:
        multihop = neo4j_client.get_joinpath_joins(candidate_fqns)
        bridge_stubs_added: set[str] = {t.get("fqn") for t in tables if t.get("fqn")}
        for mj in multihop:
            f_fqn, t_fqn = mj.get("from_fqn"), mj.get("to_fqn")
            if not (f_fqn and t_fqn):
                continue
            pair = (min(f_fqn, t_fqn), max(f_fqn, t_fqn))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            raw_clauses = mj.get("join_clauses") or []
            valid_clauses = [c for c in raw_clauses if not _has_uuid_in_join_clause(c)]
            if not valid_clauses and raw_clauses:
                logger.debug("schema_context | skip UUID multihop join | {} → {}", f_fqn, t_fqn)
                continue
            available_joins.append({
                "from": f_fqn,
                "to": t_fqn,
                "join_clauses": valid_clauses,
                "join_type": "JOIN",
                "hop_count": mj.get("hop_count", 2),
                "path_tables": mj.get("path_tables") or [],
                "is_multihop": True,
                "confidence": mj.get("quality_score"),
            })
            # Add intermediate bridge table stubs so sql_generator can reference them in FROM
            for bridge_fqn in (mj.get("path_tables") or [])[1:-1]:
                if bridge_fqn and bridge_fqn not in bridge_stubs_added:
                    tables.append({
                        "fqn": bridge_fqn,
                        "name": bridge_fqn.rsplit(".", 1)[-1],
                        "description": "bridge table for multi-hop join path",
                        "table_type": "dimension",
                    })
                    bridge_stubs_added.add(bridge_fqn)
        if multihop:
            logger.info(
                "schema_context | multihop_joins | count={} | pairs={}",
                len(multihop),
                [(mj.get("from_fqn"), mj.get("to_fqn")) for mj in multihop],
            )
    except Exception:
        pass

    unresolved_pairs = []
    for i in range(len(ir.join_clauses)):
        if not ir.join_clauses[i] and i + 1 < len(ir.path_tables):
            from_fqn = ir.path_tables[i]
            to_fqn = ir.path_tables[i + 1]
            candidate_cols = _discover_candidate_join_columns(from_fqn, to_fqn, semantic_context)
            unresolved_pairs.append({
                "from": from_fqn,
                "to": to_fqn,
                "candidate_join_columns": candidate_cols,
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
        "entity_hints": semantic_context.get("entity_hints", []),
        "_unresolved_pairs": unresolved_pairs,
    }


async def fetch_anti_patterns(state: AnalyticsState) -> str:
    # NOTE: Template gate REMOVED — anti-patterns must always run.
    # Previously gated on 'if not templates' which silently skipped guard rails
    # whenever templates were deprioritized (which is now always the case).
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_anti_patterns(embedding)
        if not patterns:
            logger.info("schema_context | anti_patterns | MISS (threshold=0.65) | thread={}", state.get("thread_id"))
            return "(none)"
        if state.get("semantic_context") is not None:
            state["semantic_context"]["anti_patterns"] = patterns
        logger.info(
            "schema_context | anti_patterns | HIT | count={} | types={} | thread={}",
            len(patterns),
            [p.get("error_type") for p in patterns],
            state.get("thread_id"),
        )
        lines = []
        for p in patterns:
            element = p.get("failing_element")
            line = f"- [{p.get('error_type', 'error')}]"
            if element:
                line += f" element={element} |"
            line += f" {p.get('error_summary', '')}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        logger.warning("schema_context | anti_patterns | ERROR | thread={} | error={}", state.get("thread_id"), e)
        return "(none)"


async def fetch_query_patterns(state: AnalyticsState) -> tuple[list, bool, str | None]:
    try:
        from app.services.agents.nodes.context_fetcher import _get_embedding
        embedding = await _get_embedding(state["question"])
        patterns = neo4j_client.search_query_patterns(embedding)
        if not patterns:
            logger.info("schema_context | query_patterns | MISS (threshold=0.65) | thread={}", state.get("thread_id"))
            return [], False, None
        if state.get("semantic_context") is not None:
            state["semantic_context"]["query_patterns"] = patterns
        top = patterns[0]
        name = top.get("intent") or top.get("id") or ""
        logger.info(
            "schema_context | query_patterns | HIT | count={} | top_intent={} | top_score={:.3f} | top_question={} | thread={}",
            len(patterns),
            name,
            top.get("score", 0),
            (top.get("question_text") or "")[:80],
            state.get("thread_id"),
        )
        return patterns, True, name
    except Exception as e:
        logger.warning("schema_context | query_patterns | ERROR | thread={} | error={}", state.get("thread_id"), e)
        return [], False, None

"""Node 2: query_compiler — builds SemanticIR and generates SQL via LLM.

Single-query only (decomposition removed — a multi-CTE SQL handles all use cases).
Filter resolution happens BEFORE SQL compilation (routes to filter_resolver if needed).

Implementation is split across:
  ir_builder.py     — SemanticIR construction, join paths, column validation, filter specs
  schema_context.py — schema context for SQL LLM, join discovery, anti/query patterns
  sql_generator.py  — CTE planner pre-pass + LLM SQL generation
"""

from __future__ import annotations

import asyncio
import re as _re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.nodes.ir_builder import build_semantic_ir
from app.services.agents.ir_utils import strip_hallucinated_columns, validate_and_fix_join_clauses
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState

_JOIN_CLAUSE_FQN_RE = _re.compile(
    r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)"
)


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
            "query_compiler | anchor_tables not in semantic_context — will attempt anyway | missing={} | thread={}",
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


async def _handle_single(state: AnalyticsState, resolved: dict, semantic_context: dict, config: RunnableConfig) -> dict:
    try:
        ir = build_semantic_ir(resolved, semantic_context)
    except Exception as e:
        logger.error("query_compiler | IR build failed | thread={} | error={}", state["thread_id"], e)
        return {"error": str(e), "needs_clarification": True, "clarification_reason": "I couldn't map your question to the data model."}

    # Validate and fix column refs + join clauses against Redshift information_schema
    # before any downstream agent sees the IR.
    # Must run BEFORE probe so we only probe columns that survived validation.
    try:
        ir = await strip_hallucinated_columns(ir, str(state["thread_id"]))
        ir = await validate_and_fix_join_clauses(ir, str(state["thread_id"]))
    except Exception as e:
        logger.warning("query_compiler | IR validation error (continuing) | thread={} | error={}", state["thread_id"], e)

    # Probe distinct values for join clause columns not yet sampled.
    # Runs on the validated IR — only probes columns confirmed to exist in Redshift.
    try:
        await _probe_join_clause_columns(ir, semantic_context, str(state["thread_id"]))
    except Exception as e:
        logger.warning("query_compiler | join_col_probe error (continuing) | thread={} | error={}", state["thread_id"], e)

    has_unresolved = any(not f.resolved for f in ir.filters)
    if ir.time_filter and not ir.time_filter.resolved:
        has_unresolved = True

    if has_unresolved:
        logger.info("query_compiler | unresolved filters | thread={} | routing to filter_resolver", state["thread_id"])
        return {
            "semantic_ir_list": [ir.model_dump()],
            "filter_resolution_needed": True,
        }

    logger.info("query_compiler | all filters resolved | thread={} | routing to sql_generator", state["thread_id"])
    return {
        "semantic_ir_list": [ir.model_dump()],
        "filter_resolution_needed": False,
    }


async def _probe_join_clause_columns(
    ir: SemanticIR,
    semantic_context: dict,
    thread_id: str,
) -> None:
    """Probe DISTINCT values for join clause columns not yet sampled.

    JoinPath join clause columns are loaded at ir_builder time — after context_fetcher
    Step B has already run. This fills the gap so the SQL LLM sees sample values for
    ALL join key columns and can verify FK relationships from value overlap.
    """
    from app.services.agents import redis_client as rc
    from app.services.agents.redshift_client import execute_query

    clauses: list[str] = list(ir.join_clauses or [])
    for path in (ir.candidate_join_paths or []):
        clauses.extend(path.get("join_clauses") or [])

    needed: dict[str, set[str]] = {}
    for clause in clauses:
        for m in _JOIN_CLAUSE_FQN_RE.finditer(clause):
            schema, table, col = m.group(1), m.group(2), m.group(3)
            needed.setdefault(f"{schema}.{table}", set()).add(col)

    if not needed:
        return

    col_index: dict[tuple[str, str], dict] = {}
    for col in (semantic_context.get("columns") or []):
        fqn = col.get("table_fqn", "")
        name = col.get("name", "")
        if fqn and name:
            col_index[(fqn, name)] = col

    # Pre-load schema caches for all referenced tables so we can skip absent columns
    # without hitting Redshift. fetch_table_schema is cached via Redis so this is cheap.
    from app.services.agents.redis_client import get_schema_cols
    schema_valid_cols: dict[str, set[str]] = {}
    for fqn in needed:
        parts = fqn.rsplit(".", 1)
        if len(parts) == 2:
            cached_schema = get_schema_cols(parts[0], parts[1])
            if cached_schema is not None:
                schema_valid_cols[fqn] = {r[0] for r in cached_schema}

    to_probe: list[tuple[str, str, dict | None]] = []
    for fqn, col_names in needed.items():
        valid_for_table = schema_valid_cols.get(fqn)
        for col_name in col_names:
            # Skip if schema cache confirms this column doesn't exist
            if valid_for_table is not None and col_name not in valid_for_table:
                logger.debug(
                    "query_compiler | join_col_probe_skip | {}.{} | confirmed absent in schema cache",
                    fqn, col_name,
                )
                continue
            key = (fqn, col_name)
            col_dict = col_index.get(key)
            if col_dict and col_dict.get("filter_values"):
                continue
            cached = rc.get_filter_values(fqn, col_name)
            if cached is not None:
                if col_dict:
                    col_dict["filter_values"] = cached
                    col_dict["sample_values"] = cached[:5]
                continue
            # col_dict may be None here — defer creating the entry until probe succeeds
            to_probe.append((fqn, col_name, col_dict))

    if not to_probe:
        return

    sem = asyncio.Semaphore(4)

    async def _probe_one(fqn: str, col_name: str, col_dict: dict | None) -> None:
        async with sem:
            probe_sql = (
                f'SELECT val FROM ('
                f'SELECT CAST("{col_name}" AS VARCHAR) AS val '
                f'FROM {fqn} '
                f'WHERE "{col_name}" IS NOT NULL '
                f'LIMIT 5000'
                f') t GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 100'
            )
            try:
                _, rows = await execute_query(probe_sql, timeout_s=60, thread_id=thread_id)
                values = [str(r[0]) for r in rows if r and r[0] is not None]
                rc.set_filter_values(fqn, col_name, values, ttl=86400)
                # Only inject a new entry into semantic_context on success — never for
                # columns that failed (probe failure = column likely doesn't exist).
                if col_dict is None:
                    col_dict = {
                        "table_fqn": fqn,
                        "name": col_name,
                        "data_type": "character varying",
                        "sample_values": values[:5],
                        "filter_values": values,
                    }
                    semantic_context.setdefault("columns", []).append(col_dict)
                else:
                    col_dict["filter_values"] = values
                    col_dict["sample_values"] = values[:5]
                logger.info(
                    "query_compiler | join_col_probe | {}.{} | count={}",
                    fqn, col_name, len(values),
                )
            except Exception as e:
                logger.warning(
                    "query_compiler | join_col_probe_failed | {}.{} | error={}",
                    fqn, col_name, e,
                )

    await asyncio.gather(*[_probe_one(fqn, col, d) for fqn, col, d in to_probe], return_exceptions=True)
    logger.info("query_compiler | join_col_probes_done | thread={} | probed={}", thread_id, len(to_probe))

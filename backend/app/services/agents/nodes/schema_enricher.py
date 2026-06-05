"""Node 1c: schema_enricher — deterministic, no LLM.

Three-tier column loading:

  Tier 1 — Anchor tables (2-4, from anchor_resolver):
    ALL columns, no cap. Loaded fresh from Neo4j.
    Stored in enriched_schema["columns"] — the ONLY thing specialists read.
    Also merged into semantic_context for sql_generator.

  Tier 2 — Hub table + multi-hop bridge tables (join columns only):
    Hub table:    only hub_join_col (e.g. lpp.bank_account.code) from cross_domain_hub.
    Bridge tables: only the columns named in JoinPath.join_clauses
                   (e.g. lpp.gl_balance.currency_code from
                   "lpp.gl_balance.currency_code = lpp.sweep_execution.currency_code").
    These are NOT loaded fresh — they are LOOKED UP from the existing _column_lookup
    that context_fetcher already built. Join-critical columns are guaranteed present
    there (T1 priority in column_loader.load_and_prioritize).
    Stored in semantic_context["_column_lookup"] ONLY.
    Specialists never see them (enriched_schema stays anchor-only).

  Tier 3 — All other discovered tables (fallback):
    Already in semantic_context from context_fetcher (capped at 12/table).
    Preserved as-is — not reloaded, not removed.
"""

from __future__ import annotations

import asyncio
import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.context import column_loader
from app.services.agents.state import AnalyticsState


def _parse_join_col_pairs(join_clauses: list[str]) -> list[tuple[str, str]]:
    """Extract (table_fqn, col_name) from join clause strings.

    Handles: "lpp.gl_balance.currency_code = lpp.sweep_execution.currency_code"
    Returns list of (fqn, col_name) for each side.
    """
    pairs: list[tuple[str, str]] = []
    for clause in (join_clauses or []):
        for side in clause.split("="):
            side = side.strip()
            # Expect schema.table.column — exactly 3 dot-separated parts
            parts = side.split(".")
            if len(parts) == 3:
                fqn = f"{parts[0]}.{parts[1]}"
                col = parts[2]
                pairs.append((fqn, col))
    return pairs


def _collect_tier2_pairs(
    anchor_set: set[str],
    hub_fqn: str | None,
    hub_join_col: str | None,
    join_paths: list[dict],
) -> list[tuple[str, str]]:
    """Build the list of (table_fqn, col_name) pairs needed for tier-2 tables.

    Only includes non-anchor tables — anchor table columns are already fully loaded.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(fqn: str, col: str) -> None:
        if fqn not in anchor_set and (fqn, col) not in seen:
            pairs.append((fqn, col))
            seen.add((fqn, col))

    # Hub table: only hub_join_col
    if hub_fqn and hub_join_col:
        _add(hub_fqn, hub_join_col)

    # Bridge/path tables: only columns referenced in JoinPath join_clauses
    for path in join_paths:
        for fqn, col in _parse_join_col_pairs(path.get("join_clauses") or []):
            _add(fqn, col)

    return pairs


async def schema_enricher(state: AnalyticsState, config: RunnableConfig) -> dict:
    anchor_tables = state.get("anchor_tables_resolved") or []
    semantic_context = state.get("semantic_context") or {}

    logger.info("schema_enricher START | thread={} | anchor_tables={}", state["thread_id"], anchor_tables)

    if not anchor_tables:
        logger.warning("schema_enricher | no anchor_tables | thread={}", state["thread_id"])
        return {"enriched_schema": {}}

    # ── Tier 1: load ALL columns for anchor tables ────────────────────────────
    try:
        anchor_cols: list[dict] = await asyncio.to_thread(
            neo4j_client.get_columns_for_tables, anchor_tables
        )
    except Exception as e:
        logger.warning("schema_enricher | anchor column load failed | error={}", e)
        return {"enriched_schema": {}}

    anchor_cols = [c for c in anchor_cols if not column_loader._is_uuid_col(c.get("name", ""))]

    if not anchor_cols:
        logger.warning("schema_enricher | no columns for anchor tables | thread={}", state["thread_id"])
        return {"enriched_schema": {}}

    # ── Tier 2: identify hub + bridge join columns ────────────────────────────
    hub_info = semantic_context.get("cross_domain_hub") or {}
    hub_fqn = hub_info.get("hub_table_fqn")
    hub_join_col = hub_info.get("hub_join_col")
    anchor_set = set(anchor_tables)

    # Find multi-hop paths between anchor tables to get bridge join columns
    join_paths: list[dict] = []
    if len(anchor_tables) >= 2:
        try:
            join_paths = await asyncio.to_thread(
                neo4j_client.get_joinpath_joins, anchor_tables
            )
        except Exception as e:
            logger.warning("schema_enricher | joinpath lookup failed | error={}", e)

    tier2_pairs = _collect_tier2_pairs(anchor_set, hub_fqn, hub_join_col, join_paths)

    # Look up tier-2 columns from existing _column_lookup (built by context_fetcher).
    # Join-critical columns (hub_join_col, join clause cols) are guaranteed present
    # because column_loader.load_and_prioritize uses T1 priority for them.
    existing_lookup: dict = semantic_context.get("_column_lookup") or {}
    tier2_cols: list[dict] = []
    missing_pairs: list[tuple[str, str]] = []

    for pair in tier2_pairs:
        col_meta = existing_lookup.get(pair)
        if col_meta:
            # Tag as join_critical so schema_context.py knows this is a join key
            col_meta = dict(col_meta)
            col_meta["_join_critical"] = True
            col_meta["_tier2_join_only"] = True
            tier2_cols.append(col_meta)
        else:
            missing_pairs.append(pair)

    if missing_pairs:
        logger.warning(
            "schema_enricher | tier2 cols not in existing lookup | missing={} | "
            "fetching from Neo4j directly (likely outside GLOBAL_CAP)",
            missing_pairs,
        )
        # Group by table and fetch join-critical columns from Neo4j.
        # These are bridge/hub join keys — only the named columns are needed,
        # not the full table schema, so we filter after loading.
        missing_tables = list({fqn for fqn, _ in missing_pairs})
        try:
            neo4j_fetched: list[dict] = await asyncio.to_thread(
                neo4j_client.get_columns_for_tables, missing_tables
            )
            missing_set = {(fqn, col) for fqn, col in missing_pairs}
            for col_meta in neo4j_fetched:
                key = (col_meta.get("table_fqn"), col_meta.get("name"))
                if key in missing_set:
                    col_meta = dict(col_meta)
                    col_meta["_join_critical"] = True
                    col_meta["_tier2_join_only"] = True
                    tier2_cols.append(col_meta)
                    logger.info(
                        "schema_enricher | tier2 fetched from neo4j | {}.{}", key[0], key[1]
                    )
        except Exception as e:
            logger.warning(
                "schema_enricher | tier2 neo4j fallback failed | missing={} | error={}",
                missing_pairs, e,
            )

    # ── Join-critical marking for anchor cols ─────────────────────────────────
    try:
        join_crit_cols = await asyncio.to_thread(
            column_loader.get_join_critical_cols,
            [{"fqn": fqn} for fqn in anchor_tables],
        )
    except Exception:
        join_crit_cols = set()

    for col in anchor_cols:
        col["_join_critical"] = (col.get("table_fqn"), col.get("name")) in join_crit_cols

    # ── Build lookups ─────────────────────────────────────────────────────────
    anchor_lookup: dict = {
        (c["table_fqn"], c["name"]): c
        for c in anchor_cols
        if c.get("table_fqn") and c.get("name")
    }
    tier2_lookup: dict = {
        (c["table_fqn"], c["name"]): c
        for c in tier2_cols
        if c.get("table_fqn") and c.get("name")
    }

    # ── enriched_schema: anchor tables ONLY ──────────────────────────────────
    # Specialists (measure/filter/dimension/directive_writer) read this.
    # Hub/bridge join columns must NOT be here — bridge FK cols would be picked
    # as filters or dimensions by the specialists.
    enriched_schema = {
        "anchor_tables": anchor_tables,
        "columns": anchor_cols,
        "_column_lookup": anchor_lookup,
        "join_critical_cols": list(join_crit_cols),
    }

    # ── semantic_context: merge all three tiers ───────────────────────────────
    # sql_generator reads this via schema_context.py.
    # primary_fqns = anchor_tables | ir.path_tables  (ir_builder adds hub/bridge to path_tables)
    # schema_context supplements from _column_lookup for all primary tables.
    updated_ctx = dict(semantic_context)

    tier2_fqns = {fqn for fqn, _ in tier2_pairs}
    enriched_fqns = anchor_set | tier2_fqns

    # _column_lookup precedence: anchor (complete) > tier2 (join cols only) > context_fetcher fallback
    merged_lookup: dict = {}
    for k, v in existing_lookup.items():
        if k[0] not in enriched_fqns:
            merged_lookup[k] = v                  # tier 3: context_fetcher fallback, capped
    merged_lookup.update(tier2_lookup)             # tier 2: hub + bridge join cols
    merged_lookup.update(anchor_lookup)            # tier 1: anchor, complete (highest precedence)
    updated_ctx["_column_lookup"] = merged_lookup

    # columns list: anchor first (complete), then tier-2 join-only cols, then context_fetcher rest
    fallback_cols = [
        c for c in (semantic_context.get("columns") or [])
        if c.get("table_fqn") not in enriched_fqns
    ]
    updated_ctx["columns"] = anchor_cols + tier2_cols + fallback_cols
    updated_ctx["join_critical_cols"] = list(join_crit_cols)

    bridge_fqns = {p[0] for p in tier2_pairs if p[0] != hub_fqn}
    logger.info(
        "schema_enricher DONE | thread={} | "
        "anchor={} anchor_cols={} | "
        "hub={} hub_col={} | "
        "bridge_tables={} bridge_cols={} | "
        "fallback_tables={}",
        state["thread_id"],
        anchor_tables, len(anchor_cols),
        hub_fqn or "none", hub_join_col or "none",
        sorted(bridge_fqns), len([c for c in tier2_cols if c.get("table_fqn") != hub_fqn]),
        len({c.get("table_fqn") for c in fallback_cols}),
    )

    return {
        "enriched_schema": enriched_schema,
        "semantic_context": updated_ctx,
    }

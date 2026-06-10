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


_ANCHOR_SEM_ORDER = {
    "amount": 0, "measure": 0, "percentage": 0, "ratio": 0,
    "dimension": 1, "code": 1, "flag": 1,
    "identifier": 2,
    "free_text": 3,
}


def _select_anchor_columns(cols: list[dict], join_critical_ids: set, max_n: int = 25) -> list[dict]:
    """3-bucket column selection per anchor table, capped at max_n.

    Bucket 1: join-critical (both FK sides via join_critical_ids) AND filter-key columns
              (code/dimension semantic_type with known values)
    Bucket 2: date/timestamp columns not in bucket 1
    Bucket 3: remaining analytical columns sorted by semantic_type value
    """
    # Group by table first — apply cap per table
    by_table: dict[str, list[dict]] = {}
    for c in cols:
        fqn = c.get("table_fqn", "")
        if fqn:
            by_table.setdefault(fqn, []).append(c)

    result: list[dict] = []
    for _, tbl_cols in by_table.items():
        result.extend(_select_anchor_cols_for_table(tbl_cols, join_critical_ids, max_n))
    return result


def _select_anchor_cols_for_table(cols: list[dict], join_critical_ids: set, max_n: int) -> list[dict]:
    def col_id(c: dict) -> tuple:
        return (c.get("table_fqn", ""), c.get("name", ""))

    def is_priority(c: dict) -> bool:
        if col_id(c) in join_critical_ids:
            return True
        if c.get("referenced_table_fqn"):
            return True
        sem = c.get("semantic_type", "")
        if sem in ("code", "dimension") and (c.get("value_vocabulary") or c.get("distinct_values")):
            return True
        return False

    bucket1 = [c for c in cols if is_priority(c)]
    b1_ids = {col_id(c) for c in bucket1}

    bucket2 = [
        c for c in cols
        if col_id(c) not in b1_ids
        and (
            "date" in (c.get("data_type") or "").lower()
            or "timestamp" in (c.get("data_type") or "").lower()
        )
    ]
    b2_ids = b1_ids | {col_id(c) for c in bucket2}

    bucket3 = sorted(
        [c for c in cols if col_id(c) not in b2_ids],
        key=lambda c: _ANCHOR_SEM_ORDER.get(c.get("semantic_type", ""), 4),
    )

    remaining = max(0, max_n - len(bucket1) - len(bucket2))
    return bucket1 + bucket2 + bucket3[:remaining]


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

    # Find all confirmed join paths between anchor tables (JOINS_TO + JoinPath)
    anchor_join_paths: list[dict] = []
    if len(anchor_tables) >= 2:
        try:
            anchor_join_paths = await asyncio.to_thread(
                neo4j_client.get_all_join_paths_for_tables, anchor_tables
            )
            logger.info(
                "schema_enricher | anchor_join_paths | count={} | pairs={}",
                len(anchor_join_paths),
                [(p.get("from_fqn", "").rsplit(".", 1)[-1], p.get("to_fqn", "").rsplit(".", 1)[-1]) for p in anchor_join_paths],
            )
        except Exception as e:
            logger.warning("schema_enricher | anchor join paths lookup failed | error={}", e)

    # Value-overlap fallback for anchor pairs with no explicit JoinPath in Neo4j.
    # Discovers join conditions from actual column value overlap — handles tables
    # where FK edges aren't yet modeled in the knowledge graph.
    # Stored under candidate_overlap_joins (NOT anchor_join_paths) so structural
    # JoinPath entries always win and heuristic currency joins can't override them.
    candidate_overlap_joins: list[dict] = []
    if len(anchor_tables) >= 2:
        resolved_pairs: set[tuple] = set()
        for p in anchor_join_paths:
            resolved_pairs.add((p["from_fqn"], p["to_fqn"]))
            resolved_pairs.add((p["to_fqn"], p["from_fqn"]))
        for i, fqn_a in enumerate(anchor_tables):
            for fqn_b in anchor_tables[i + 1:]:
                if (fqn_a, fqn_b) not in resolved_pairs:
                    try:
                        overlap = await asyncio.to_thread(
                            neo4j_client.find_join_by_value_overlap, fqn_a, fqn_b
                        )
                        if overlap:
                            logger.info(
                                "schema_enricher | value_overlap_join | {}<->{} | candidates={}",
                                fqn_a, fqn_b, overlap[:2],
                            )
                            candidate_overlap_joins.append({
                                "from_fqn": fqn_a,
                                "to_fqn":   fqn_b,
                                "join_clauses": [
                                    f"{fqn_a}.{c['from_col']} = {fqn_b}.{c['to_col']}"
                                    for c in overlap[:1]
                                ],
                                "source": "value_overlap",
                            })
                            resolved_pairs.add((fqn_a, fqn_b))
                            resolved_pairs.add((fqn_b, fqn_a))
                        else:
                            logger.warning(
                                "schema_enricher | unresolved_pair | {}<->{} | no join in graph or value_overlap",
                                fqn_a, fqn_b,
                            )
                    except Exception as e:
                        logger.warning(
                            "schema_enricher | value_overlap_join failed | {}<->{} | error={}",
                            fqn_a, fqn_b, e,
                        )

    # For backward compat: join_paths for tier-2 bridge extraction uses anchor_join_paths
    join_paths = anchor_join_paths

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
        # Build filter_values for anchor cols (same as column_loader does for context_fetcher cols)
        if "filter_values" not in col:
            col["filter_values"] = column_loader.get_filter_values(col)

    # ── Apply 25-col cap per anchor table (specialists view) ──────────────────
    # Full anchor_cols go into _column_lookup (sql_generator supplement).
    # Capped display_cols go into enriched_schema (specialists read this).
    # Schema_context.py supplements sql_generator from _column_lookup for primary tables.
    anchor_display_cols = _select_anchor_columns(anchor_cols, join_crit_cols, max_n=25)

    # ── Build lookups ─────────────────────────────────────────────────────────
    anchor_lookup: dict = {
        (c["table_fqn"], c["name"]): c
        for c in anchor_cols  # FULL data — not capped — for sql_generator supplement
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
        "columns": anchor_display_cols,  # 25-col capped for specialists
        "_column_lookup": anchor_lookup,
        "join_critical_cols": list(join_crit_cols),
    }

    for t in anchor_tables:
        t_cols = [c for c in anchor_cols if c.get("table_fqn") == t]
        t_display = [c for c in anchor_display_cols if c.get("table_fqn") == t]
        logger.info("schema_enricher | anchor_col_cap | {} | all={} display={}", t, len(t_cols), len(t_display))

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

    # ── A5: BusinessTerm concept mappings for anchor tables ───────────────────
    # Fetches BTs linked to anchor tables via REFERENCES_TABLE edges.
    # concept_mappings: {term: {definition, computation, table_fqn}}
    # directive_writer uses this to emit COMPUTATION: instead of SCHEMA_GAP_CONCEPT.
    concept_mappings: dict = {}
    try:
        bt_rows = await asyncio.to_thread(neo4j_client.get_business_terms_for_tables, anchor_tables)
        for row in bt_rows:
            term = row.get("term") or ""
            if term:
                concept_mappings[term] = {
                    "definition": row.get("definition") or "",
                    "computation": row.get("computation") or row.get("sql_expression") or "",
                    "table_fqn": row.get("table_fqn") or "",
                    "term_type": row.get("term_type") or "",
                }
        if concept_mappings:
            logger.info("schema_enricher | concept_mappings | count={} | terms={}", len(concept_mappings), list(concept_mappings.keys())[:5])
    except Exception as e:
        logger.warning("schema_enricher | concept_mappings fetch failed | error={}", e)

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
        "anchor_join_paths": anchor_join_paths,
        "candidate_overlap_joins": candidate_overlap_joins,
        "concept_mappings": concept_mappings or None,
    }

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
from app.services.agents.helpers import merge_neo4j_raw_graph
from app.services.agents.join_key_filter import filter_join_paths as _jk_filter, is_clause_safe as _jk_clause_safe
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


def _bfs_transitive_join(
    adj: dict,
    src: str,
    dst: str,
    max_hops: int = 4,
) -> dict | None:
    """BFS through resolved anchor join graph to find a transitive path.

    Returns {join_clauses, path_tables, hop_count, source} or None if unreachable.
    Only traverses edges already present in adj — no additional queries.
    Pure Python: uses collections.deque, no regex.
    """
    from collections import deque as _deque
    if src not in adj:
        return None
    queue = _deque([(src, [src], [])])
    while queue:
        current, visited_tables, acc_clauses = queue.popleft()
        if len(visited_tables) > max_hops + 1:
            continue
        for neighbor, edge in (adj.get(current) or {}).items():
            if neighbor in visited_tables:
                continue
            new_clauses = acc_clauses + edge["join_clauses"]
            new_tables  = visited_tables + [neighbor]
            if neighbor == dst:
                return {
                    "join_clauses": new_clauses,
                    "path_tables":  new_tables,
                    "hop_count":    len(new_clauses),
                    "source":       "derived_transitive",
                }
            queue.append((neighbor, new_tables, new_clauses))
    return None


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

    # Log per-table raw neo4j column counts immediately after load (before any selection/pruning)
    _neo4j_counts = {}
    for _c in anchor_cols:
        _t = _c.get("table_fqn") or ""
        _neo4j_counts[_t] = _neo4j_counts.get(_t, 0) + 1
    for _t in anchor_tables:
        _n = _neo4j_counts.get(_t, 0)
        if _n == 0:
            logger.warning(
                "schema_enricher | neo4j_zero_columns | table={} | "
                "no columns returned — HAS_COLUMN edge direction issue? "
                "Run: MATCH (c:Column)-[r:HAS_COLUMN]->(t:Table {{fqn:'{}'}}) "
                "CREATE (t)-[:HAS_COLUMN]->(c) DELETE r",
                _t, _t,
            )
        else:
            logger.info("schema_enricher | neo4j_col_count | table={} | count={}", _t, _n)

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

        # ── Profile filter A: block dangerous join paths from Neo4j ──────────────
        _before_filter = len(anchor_join_paths)
        anchor_join_paths, _jk_blocked = _jk_filter(anchor_join_paths)
        for _blk in _jk_blocked:
            logger.warning(
                "schema_enricher | profile_filter | BLOCKED {}<->{} | verdict=dead_join | reasons={} | thread={}",
                _blk.get("from_fqn", "?"), _blk.get("to_fqn", "?"),
                _blk.get("_blocked_by", []), state["thread_id"],
            )
        if _before_filter > 0:
            _n_safe    = sum(1 for p in anchor_join_paths if p.get("_path_verdict") == "safe")
            _n_caution = sum(1 for p in anchor_join_paths if p.get("_path_verdict") == "caution")
            _n_fanout  = sum(1 for p in anchor_join_paths if p.get("_path_verdict") == "fan_out")
            _n_unknown = sum(1 for p in anchor_join_paths if p.get("_path_verdict") == "unknown")
            logger.info(
                "schema_enricher | profile_filter | total={} safe={} caution={} fan_out={} unknown={} dead_join={} | thread={}",
                _before_filter, _n_safe, _n_caution, _n_fanout, _n_unknown, len(_jk_blocked), state["thread_id"],
            )
            for _p in anchor_join_paths:
                _verdict = (_p.get("_path_verdict") or "unknown").upper()
                logger.info(
                    "schema_enricher | profile_filter | {} {}<->{} | clauses={}",
                    _verdict, _p.get("from_fqn", "?"), _p.get("to_fqn", "?"), _p.get("join_clauses", []),
                )

    # Three-tier join resolution for anchor pairs not covered by explicit JoinPath in Neo4j:
    #   Tier 1 (heuristic):   value-overlap from Redshift DISTINCT probes -> candidate_overlap_joins
    #   Tier 2 (structural):  BFS through already-resolved anchor edges -> anchor_join_paths
    #   Tier 3 (structural):  Neo4j shortestPath through non-anchor intermediate tables -> anchor_join_paths
    #
    # Structural paths (Tiers 2+3) go to anchor_join_paths so ir_builder picks them up.
    # Heuristic value-overlap goes to candidate_overlap_joins only (display fallback).
    # Tier 2+3 run for ALL pairs not yet in anchor_join_paths, even if value_overlap found
    # a heuristic match, because candidate_overlap_joins is not consumed by ir_builder.
    candidate_overlap_joins: list[dict] = []
    if len(anchor_tables) >= 2:
        # structural_pairs: pairs with confirmed structural join (feeds ir_builder join chain).
        # Only mark as structural when actual join clauses exist — a JoinPath node with empty
        # join_clauses must NOT block Tier 2 BFS or Tier 3 shortestPath (H2 fix).
        structural_pairs: set[tuple] = set()
        for p in anchor_join_paths:
            _f2, _t2 = p.get("from_fqn"), p.get("to_fqn")
            if not (_f2 and _t2):
                continue
            _cl2 = list(p.get("join_clauses") or [])
            if not _cl2 and p.get("from_col") and p.get("to_col"):
                _cl2 = [f"{_f2}.{p['from_col']} = {_t2}.{p['to_col']}"]
            if _cl2:
                structural_pairs.add((_f2, _t2))
                structural_pairs.add((_t2, _f2))

        # resolved_pairs: structural + heuristic (avoids redundant value_overlap calls)
        resolved_pairs: set[tuple] = set(structural_pairs)

        # Build BFS adjacency from anchor_join_paths (both directions)
        _adj: dict[str, dict] = {}
        for _p in anchor_join_paths:
            _f, _t = _p.get("from_fqn"), _p.get("to_fqn")
            _cl = list(_p.get("join_clauses") or [])
            if not _cl and _p.get("from_col") and _p.get("to_col") and _f and _t:
                _cl = [f"{_f}.{_p['from_col']} = {_t}.{_p['to_col']}"]
            _pt = list(_p.get("path_tables") or ([_f, _t] if _f and _t else []))
            if _f and _t and _cl:
                _adj.setdefault(_f, {})[_t] = {"join_clauses": _cl, "path_tables": _pt}
                _adj.setdefault(_t, {})[_f] = {
                    "join_clauses": list(reversed(_cl)),
                    "path_tables":  list(reversed(_pt)),
                }

        for i, fqn_a in enumerate(anchor_tables):
            for fqn_b in anchor_tables[i + 1:]:
                # Tier 1 (heuristic): value-overlap — only when no join found yet at all
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
                    except Exception as e:
                        logger.warning(
                            "schema_enricher | value_overlap_join failed | {}<->{} | error={}",
                            fqn_a, fqn_b, e,
                        )

                # Tier 2 (structural BFS): derive path through already-resolved anchor edges.
                # Runs for ALL pairs not yet in anchor_join_paths — value_overlap result irrelevant
                # because candidate_overlap_joins is not consumed by ir_builder.
                if (fqn_a, fqn_b) not in structural_pairs:
                    transitive = _bfs_transitive_join(_adj, fqn_a, fqn_b)
                    if transitive:
                        logger.info(
                            "schema_enricher | transitive_join | {}<->{} | via={} | hops={}",
                            fqn_a, fqn_b, transitive["path_tables"], transitive["hop_count"],
                        )
                        new_path = {"from_fqn": fqn_a, "to_fqn": fqn_b, **transitive}
                        anchor_join_paths.append(new_path)
                        structural_pairs.add((fqn_a, fqn_b))
                        structural_pairs.add((fqn_b, fqn_a))
                        _adj.setdefault(fqn_a, {})[fqn_b] = {
                            "join_clauses": transitive["join_clauses"],
                            "path_tables":  transitive["path_tables"],
                        }
                        _adj.setdefault(fqn_b, {})[fqn_a] = {
                            "join_clauses": list(reversed(transitive["join_clauses"])),
                            "path_tables":  list(reversed(transitive["path_tables"])),
                        }
                    elif (fqn_a, fqn_b) not in resolved_pairs:
                        # Tier 3 (structural Neo4j shortestPath): last resort through non-anchor tables.
                        # Skipped when Tier 1 (value_overlap) already found a heuristic path for this
                        # pair — a 1-hop heuristic join is better than a 3-hop graph traversal path.
                        try:
                            graph_path = await asyncio.to_thread(
                                neo4j_client.find_join_via_graph_traversal, fqn_a, fqn_b
                            )
                            if graph_path and graph_path.get("join_clauses"):
                                logger.info(
                                    "schema_enricher | graph_traversal_join | {}<->{} | path={} | hops={}",
                                    fqn_a, fqn_b, graph_path["path_tables"], graph_path.get("hop_count"),
                                )
                                anchor_join_paths.append(graph_path)
                                structural_pairs.add((fqn_a, fqn_b))
                                structural_pairs.add((fqn_b, fqn_a))
                                _adj.setdefault(fqn_a, {})[fqn_b] = {
                                    "join_clauses": graph_path["join_clauses"],
                                    "path_tables":  graph_path["path_tables"],
                                }
                                _adj.setdefault(fqn_b, {})[fqn_a] = {
                                    "join_clauses": list(reversed(graph_path["join_clauses"])),
                                    "path_tables":  list(reversed(graph_path["path_tables"])),
                                }
                            else:
                                logger.warning(
                                    "schema_enricher | unresolved_pair | {}<->{} | exhausted all tiers",
                                    fqn_a, fqn_b,
                                )
                        except Exception as _e:
                            logger.warning(
                                "schema_enricher | graph_traversal_join failed | {}<->{} | error={}",
                                fqn_a, fqn_b, _e,
                            )
                            logger.warning(
                                "schema_enricher | unresolved_pair | {}<->{} | no structural path found",
                                fqn_a, fqn_b,
                            )

    # ── N1: Null-join-key table pruning ──────────────────────────────────────────
    # For character/text join columns, if the semantic model sampler found zero distinct
    # values AND zero sample values, no non-null rows were found -> JOINs produce 0 rows.
    # has_data and null_frac are unreliable signals (semantic model generation bug can set
    # has_data=False on columns that have real data). Pure string ops, no regex.

    def _col_has_joinable_data(c: dict) -> bool:
        dtype = (c.get("data_type") or "").lower()
        name  = (c.get("name") or "").lower()
        is_string_col = "char" in dtype or "text" in dtype
        if not is_string_col:
            return True
        if "uuid" in name:
            return True
        # has_data=True means the semantic model generator confirmed non-null rows exist
        # (null_frac < 0.95 AND n_distinct != 0). Trust it even when distinct_values is
        # empty — empty vocab means the column wasn't sampled, not that it has no data.
        if c.get("has_data") is True:
            return True
        distinct = c.get("distinct_values") or []
        sample   = c.get("sample_values") or []
        return bool(distinct) or bool(sample)

    _col_data_map: dict[tuple, bool] = {
        (c.get("table_fqn") or "", c.get("name") or ""): _col_has_joinable_data(c)
        for c in anchor_cols
        if c.get("table_fqn") and c.get("name")
    }
    _null_join_tables: set[str] = set()
    for _jp in anchor_join_paths:
        for _clause in (_jp.get("join_clauses") or []):
            for _side in _clause.split("="):
                _side_stripped = _side.strip()
                _jparts = _side_stripped.split(".")
                if len(_jparts) == 3:
                    _jfqn = _jparts[0] + "." + _jparts[1]
                    _jcol = _jparts[2]
                    if _jfqn in anchor_set and not _col_data_map.get((_jfqn, _jcol), True):
                        _null_join_tables.add(_jfqn)

    if _null_join_tables:
        logger.warning(
            "schema_enricher | null_join_pruning | tables_with_empty_join_cols={} | "
            "pruning only their join PATHS (tables remain as anchors) | thread={}",
            sorted(_null_join_tables), state["thread_id"],
        )
        # Prune only the join paths that use empty-column joins.
        # Never remove anchor tables themselves — anchor_resolver selected them for
        # semantic relevance; removing them causes cascading column-loading failures
        # (specialists see no schema for those tables -> directive infers wrong columns).
        anchor_join_paths = [
            p for p in anchor_join_paths
            if p.get("from_fqn") not in _null_join_tables
            and p.get("to_fqn") not in _null_join_tables
        ]

    # ── N1-ext: Null-join-key pruning for bridge (intermediate) table join columns ──
    # Multi-hop paths from G1 BFS/graph traversal include bridge tables not in anchor_set.
    # If a bridge join column has no sampled data, the entire path produces 0 rows.
    # Prune the path entry (not the anchor table) when a null bridge join column is found.
    _bridge_fqns: set[str] = set()
    for _jp in anchor_join_paths:
        for _pt in (_jp.get("path_tables") or []):
            if _pt and _pt not in anchor_set:
                _bridge_fqns.add(_pt)

    _bridge_neo4j_cols: list[dict] = []
    if _bridge_fqns:
        try:
            _bridge_cols = await asyncio.to_thread(
                neo4j_client.get_columns_for_tables, sorted(_bridge_fqns)
            )
            _bridge_neo4j_cols = _bridge_cols or []
            for _bc in _bridge_neo4j_cols:
                _bfqn = _bc.get("table_fqn") or ""
                _bname = _bc.get("name") or ""
                if _bfqn and _bname:
                    _col_data_map[(_bfqn, _bname)] = _col_has_joinable_data(_bc)
        except Exception as _be:
            logger.debug("schema_enricher | bridge_col_load_failed | error={}", _be)

    _null_bridge_path_indices: set[int] = set()
    for _i, _jp in enumerate(anchor_join_paths):
        for _clause in (_jp.get("join_clauses") or []):
            for _side in _clause.split("="):
                _side_stripped = _side.strip()
                _jparts = _side_stripped.split(".")
                if len(_jparts) == 3:
                    _jfqn = _jparts[0] + "." + _jparts[1]
                    _jcol = _jparts[2]
                    if _jfqn in _bridge_fqns and not _col_data_map.get((_jfqn, _jcol), True):
                        _null_bridge_path_indices.add(_i)
                        logger.warning(
                            "schema_enricher | null_bridge_join_pruning | path_idx={} | bridge={} | col={} | thread={}",
                            _i, _jfqn, _jcol, state["thread_id"],
                        )

    if _null_bridge_path_indices:
        anchor_join_paths = [p for i, p in enumerate(anchor_join_paths) if i not in _null_bridge_path_indices]

    # ── Rescue pruned pairs using value-overlap candidates ────────────────────
    # After both pruning passes, some anchor table pairs may have lost ALL their
    # join paths (formal paths had null columns; transitive paths used the same
    # null column as a bridge hop). If a value_overlap candidate exists for that
    # pair, promote it to anchor_join_paths so ir_builder can use it.
    # "join column null" means the FORMAL path fails — the overlap column may be
    # a perfectly valid alternative join key (e.g. currency_code = threshold_currency).
    _surviving_pairs: set[tuple] = set()
    for _p in anchor_join_paths:
        _f, _t = _p.get("from_fqn"), _p.get("to_fqn")
        if _f and _t:
            _surviving_pairs.add((_f, _t))
            _surviving_pairs.add((_t, _f))

    _rescued = 0
    for _cand in candidate_overlap_joins:
        _cf, _ct = _cand.get("from_fqn"), _cand.get("to_fqn")
        if not (_cf and _ct):
            continue
        if (_cf, _ct) in _surviving_pairs:
            continue  # already has a path
        if _cf not in anchor_set or _ct not in anchor_set:
            continue  # only rescue anchor-to-anchor pairs
        _rescue_clauses = _cand.get("join_clauses") or []

        # ── Profile filter B: block dangerous rescue clauses ──────────────────
        _safe_rescue = []
        for _rc in _rescue_clauses:
            _ok, _reason = _jk_clause_safe(_rc)
            if _ok:
                _safe_rescue.append(_rc)
                logger.debug(
                    "schema_enricher | rescue_filter | ALLOWED clause={} | {}<->{}",
                    _rc, _cf, _ct,
                )
            else:
                logger.warning(
                    "schema_enricher | rescue_filter | BLOCKED clause={} | reason={} | {}<->{} | thread={}",
                    _rc, _reason, _cf, _ct, state["thread_id"],
                )
        if not _safe_rescue:
            logger.info(
                "schema_enricher | rescue_blocked_by_profile | all clauses dangerous | {}<->{} | thread={}",
                _cf, _ct, state["thread_id"],
            )
            continue  # don't rescue this pair at all

        _is_categorical = any(
            any(
                part.strip().rsplit(".", 1)[-1].lower().endswith(suf)
                for suf in ("_currency", "_type", "_status", "_code", "_category")
            )
            for clause in _safe_rescue
            for part in clause.split("=")
        )
        _rescue_entry: dict = {
            "from_fqn": _cf,
            "to_fqn":   _ct,
            "join_clauses": _safe_rescue,
            "path_tables": [_cf, _ct],
            "source": "value_overlap_rescue",
        }
        if _is_categorical:
            _rescue_entry["join_quality"] = "categorical_overlap"
        anchor_join_paths.append(_rescue_entry)
        _surviving_pairs.add((_cf, _ct))
        _surviving_pairs.add((_ct, _cf))
        _rescued += 1
        logger.info(
            "schema_enricher | value_overlap_rescue | {}<->{} | "
            "promoted after formal paths pruned | clauses={}",
            _cf, _ct, _cand.get("join_clauses"),
        )

    if _rescued:
        logger.info(
            "schema_enricher | rescued {} pairs via value_overlap after null-join pruning | thread={}",
            _rescued, state["thread_id"],
        )

    # ── Profile filter C: final re-evaluation after all join resolution ───────────
    # Filter A ran when clauses were mostly empty (raw Neo4j paths → _path_verdict="unknown").
    # BFS/transitive/graph_traversal has now populated actual join clauses.
    # Re-run to: (1) log observable verdicts, (2) block newly-found dead_join paths,
    # (3) annotate fan_out paths so sql_generator uses pre-agg CTE instead of direct JOIN.
    if anchor_join_paths:
        _c_allowed, _c_blocked = _jk_filter(anchor_join_paths)
        for _blk in _c_blocked:
            logger.warning(
                "schema_enricher | join_verdict | DEAD_JOIN BLOCKED {}<->{} | clauses={} | reasons={} | thread={}",
                _blk.get("from_fqn", "?"), _blk.get("to_fqn", "?"),
                _blk.get("join_clauses", []),
                _blk.get("_blocked_by", []),
                state["thread_id"],
            )
        for _ap in _c_allowed:
            _v = (_ap.get("_path_verdict") or "unknown").upper()
            _ap_clauses = _ap.get("join_clauses") or []
            if _v == "FAN_OUT":
                logger.warning(
                    "schema_enricher | join_verdict | FAN_OUT {}<->{} | clauses={} | pre-agg CTE required | thread={}",
                    _ap.get("from_fqn", "?"), _ap.get("to_fqn", "?"), _ap_clauses, state["thread_id"],
                )
            elif _v in ("CAUTION", "SAFE"):
                logger.info(
                    "schema_enricher | join_verdict | {} {}<->{} | clauses={}",
                    _v, _ap.get("from_fqn", "?"), _ap.get("to_fqn", "?"), _ap_clauses,
                )
            else:
                logger.info(
                    "schema_enricher | join_verdict | UNKNOWN {}<->{} | clauses={} | not in profile",
                    _ap.get("from_fqn", "?"), _ap.get("to_fqn", "?"), _ap_clauses,
                )
        _c_safe    = sum(1 for p in _c_allowed if p.get("_path_verdict") == "safe")
        _c_caution = sum(1 for p in _c_allowed if p.get("_path_verdict") == "caution")
        _c_fanout  = sum(1 for p in _c_allowed if p.get("_path_verdict") == "fan_out")
        _c_unknown = sum(1 for p in _c_allowed if p.get("_path_verdict") == "unknown")
        logger.info(
            "schema_enricher | join_verdicts_final | safe={} caution={} fan_out={} unknown={} dead_join={} | thread={}",
            _c_safe, _c_caution, _c_fanout, _c_unknown, len(_c_blocked), state["thread_id"],
        )
        anchor_join_paths = _c_allowed

    # ── Cascade prune: remove signal-injected tables with no surviving dead-safe path ──
    # Rules:
    #   1. LLM-selected tables are NEVER pruned — the LLM chose them for a reason.
    #      Fan-out paths to LLM-selected tables are annotated; sql_generator handles them.
    #   2. fx_rate is exempt — it uses a template join, not a structural JOINS_TO path.
    #   3. Signal-injected tables with ONLY dead_join paths blocked and no remaining paths
    #      are pruned — joining to them produces 0 rows and they can't answer anything.
    #   4. Fan-out annotated paths still count as "connected" — the table stays; the join
    #      annotation tells sql_generator to use pre-agg CTE instead of direct JOIN.
    _FX_RATE_FQN = "lpp.fx_rate"
    _llm_selected = set(state.get("llm_selected_anchors") or [])
    _cascade_exempt: set[str] = _llm_selected.copy()
    if state.get("fx_rate_template_join"):
        _cascade_exempt.add(_FX_RATE_FQN)

    if len(anchor_tables) > 1:
        # All paths (including fan_out annotated) count as connected
        _path_connected: set[str] = set()
        for _p in anchor_join_paths:
            _p_from = _p.get("from_fqn") or ""
            _p_to   = _p.get("to_fqn") or ""
            if _p_from:
                _path_connected.add(_p_from)
            if _p_to:
                _path_connected.add(_p_to)

        # Always keep the primary anchor regardless
        if anchor_tables:
            _path_connected.add(anchor_tables[0])

        _cascade_pruned = [
            t for t in anchor_tables
            if t not in _path_connected and t not in _cascade_exempt
        ]
        if _cascade_pruned:
            logger.warning(
                "schema_enricher | cascade_prune | removing={} (signal-injected, no surviving join path) | "
                "keeping={} | thread={}",
                _cascade_pruned,
                [t for t in anchor_tables if t not in _cascade_pruned],
                state["thread_id"],
            )
            anchor_tables = [t for t in anchor_tables if t not in _cascade_pruned]
            anchor_set = set(anchor_tables)
            anchor_cols = [c for c in anchor_cols if c.get("table_fqn") in anchor_set]
        else:
            logger.info(
                "schema_enricher | cascade_prune | all tables connected, none pruned | thread={}",
                state["thread_id"],
            )

    # Surface fan-out annotated joins for sql_generator.
    # fx_rate is excluded — it has its own dedicated template join block (ROW_NUMBER window)
    # and doesn't need the generic fan-out warning in the blueprint.
    _fx_template_exempt: set[str] = {_FX_RATE_FQN} if state.get("fx_rate_template_join") else set()
    _fan_out_annotated_fqns: set[str] = set()
    for _p in anchor_join_paths:
        if _p.get("_join_annotation") == "pre_agg_required":
            for _side in ("from_fqn", "to_fqn"):
                _fqn = _p.get(_side) or ""
                if _fqn and _fqn not in _fx_template_exempt:
                    _fan_out_annotated_fqns.add(_fqn)
    if _fan_out_annotated_fqns:
        logger.warning(
            "schema_enricher | fan_out_joins | tables={} — need temporal/pre-agg join pattern | thread={}",
            sorted(_fan_out_annotated_fqns), state["thread_id"],
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

    _tier2_neo4j_fetched: list[dict] = []
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
                    _tier2_neo4j_fetched.append(col_meta)
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

    # ── DISTKEY annotation (L2) — fetch per anchor table, annotate column metadata ─
    # Helps sql_generator prefer join columns that are DISTKEYs (avoids DS_DIST_BOTH).
    # Fails silently when pg_table_def is unavailable (Redshift Serverless).
    try:
        from app.services.agents.redshift_client import fetch_table_distkeys as _fetch_distkeys
        _distkey_maps: dict[str, dict[str, bool]] = {}
        for _t_fqn in anchor_tables:
            _parts = _t_fqn.split(".", 1)
            if len(_parts) == 2:
                _dk_map = await _fetch_distkeys(_parts[0], _parts[1])
                if _dk_map:
                    _distkey_maps[_t_fqn] = _dk_map
        if _distkey_maps:
            for col in anchor_cols:
                _fqn = col.get("table_fqn", "")
                _col_name = col.get("name", "")
                if _fqn in _distkey_maps and _distkey_maps[_fqn].get(_col_name):
                    col["is_distkey"] = True
    except Exception as _dk_err:
        logger.debug("schema_enricher | distkey annotation failed | error={}", _dk_err)

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

    # ── Layer 8: table_grains + table_row_counts for specialists ──────────────
    _ctx_tables_seq = semantic_context.get("tables") or []
    _table_grains: dict = {
        t.get("fqn"): t.get("grain") or ""
        for t in _ctx_tables_seq
        if t.get("fqn") and t.get("grain")
    }
    _table_row_counts: dict = {
        t.get("fqn"): t.get("row_count") or 0
        for t in _ctx_tables_seq
        if t.get("fqn")
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
        "table_grains": _table_grains,
        "table_row_counts": _table_row_counts,
    }

    for t in anchor_tables:
        t_cols = [c for c in anchor_cols if c.get("table_fqn") == t]
        t_display = [c for c in anchor_display_cols if c.get("table_fqn") == t]
        logger.info("schema_enricher | anchor_col_cap | {} | all={} display={}", t, len(t_cols), len(t_display))
        if len(t_cols) < 3:
            logger.warning(
                "schema_enricher | sparse_anchor_columns | table={} | cols={} | "
                "Neo4j HAS_COLUMN edges may be stored in wrong direction — "
                "run: MATCH (c:Column)-[r:HAS_COLUMN]->(t:Table) WHERE NOT (t)-[:HAS_COLUMN]->(c) "
                "CREATE (t)-[:HAS_COLUMN]->(c) DELETE r RETURN count(*)",
                t, [c.get("name") for c in t_cols],
            )

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
                    "definition": row.get("description") or "",
                    "table_fqn": row.get("table_fqn") or "",
                    "term_type": row.get("term_type") or "",
                    "term_category": row.get("term_category") or "",
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

    # ── Accumulate raw Neo4j graph data for trust visualization ──────────────
    _raw_nodes: list[dict] = []
    _raw_edges: list[dict] = []

    # Column nodes from anchor tables + HAS_COLUMN edges
    for _c in anchor_cols:
        if _c.get("table_fqn") and _c.get("name"):
            _raw_nodes.append({"_label": "Column", **_c})
            _raw_edges.append({"_type": "HAS_COLUMN", "table_fqn": _c["table_fqn"], "column_name": _c["name"]})

    # Column nodes from bridge tables (N1-ext null pruning load)
    for _c in _bridge_neo4j_cols:
        if _c.get("table_fqn") and _c.get("name"):
            _raw_nodes.append({"_label": "Column", **_c})

    # Column nodes from tier2 Neo4j fallback fetch (outside GLOBAL_CAP)
    for _c in _tier2_neo4j_fetched:
        if _c.get("table_fqn") and _c.get("name"):
            _raw_nodes.append({"_label": "Column", "_tier2_fallback": True, **_c})

    # JoinPath nodes from anchor_join_paths
    for _jp in anchor_join_paths:
        _raw_nodes.append({"_label": "JoinPath", "source": _jp.get("source", "schema_enricher"), **_jp})

    # JOINS_TO edges from value-overlap candidates
    for _cand in candidate_overlap_joins:
        _raw_edges.append({
            "_type": "JOINS_TO", "source": "value_overlap",
            "from_fqn": _cand.get("from_fqn", ""), "to_fqn": _cand.get("to_fqn", ""),
            "from_col": "", "to_col": "",
        })

    # BusinessTerm nodes + REFERENCES_TABLE edges (from concept_mappings)
    for _term, _meta in (concept_mappings or {}).items():
        _raw_nodes.append({"_label": "BusinessTerm", "term": _term, **_meta})
        _raw_edges.append({"_type": "REFERENCES_TABLE", "term": _term, "table_fqn": _meta.get("table_fqn", "")})

    # SEMANTICALLY_SIMILAR edges (uses get_semantically_similar_columns, not previously called in pipeline)
    _anchor_col_ids = [_c.get("id") for _c in anchor_cols if _c.get("id")]
    if _anchor_col_ids:
        try:
            _sem_sim = await asyncio.to_thread(neo4j_client.get_semantically_similar_columns, _anchor_col_ids)
            _raw_edges.extend({"_type": "SEMANTICALLY_SIMILAR", **r} for r in (_sem_sim or []))
        except Exception as _sse:
            logger.debug("schema_enricher | semantically_similar fetch skipped | error={}", _sse)

    neo4j_raw_graph = merge_neo4j_raw_graph(
        state.get("neo4j_raw_graph") or {},
        _raw_nodes,
        _raw_edges,
    )

    return {
        "enriched_schema": enriched_schema,
        "semantic_context": updated_ctx,
        "anchor_join_paths": anchor_join_paths,
        "anchor_tables_resolved": anchor_tables,
        "candidate_overlap_joins": candidate_overlap_joins,
        "concept_mappings": concept_mappings or None,
        "neo4j_raw_graph": neo4j_raw_graph,
        "fan_out_annotated_fqns": sorted(_fan_out_annotated_fqns) if _fan_out_annotated_fqns else None,
    }

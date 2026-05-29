"""Shared IR validation utilities.

Provides two async functions used by query_compiler immediately after build_semantic_ir:
  - strip_hallucinated_columns: validates / fuzzy-remaps ColumnRef objects against Redshift
  - validate_and_fix_join_clauses: validates / fuzzy-remaps join clause column names

Both rely on the Redis schema_cols cache (key: schema_cols:{schema}.{table}) which is
populated by context_fetcher._enrich_columns_from_redshift before these run.
"""

from __future__ import annotations

import asyncio
import re

from app.core.logger import logger
from app.services.agents.semantic_ir import SemanticIR


# ── strip_hallucinated_columns ────────────────────────────────────────────────

async def strip_hallucinated_columns(ir: SemanticIR, thread_id: str) -> SemanticIR:
    """Cross-check every ColumnRef in the IR against Redshift information_schema.

    Corrections applied:
      1. Fuzzy remap  — column exists under a close name (rapidfuzz WRatio >= 75)
      2. Drop         — no match found

    Non-fatal: on any Redshift error the original IR is returned unchanged.
    Returns the corrected IR (replaces the caller's reference).
    """
    from app.services.agents.redshift_client import get_table_columns
    from app.services.agents.redis_client import get_schema_cols

    table_cols: dict[str, set[str]] = {}

    def _register(fqn: str, col: str) -> None:
        if fqn and col:
            table_cols.setdefault(fqn, set()).add(col)

    for m in ir.measures:
        _register(m.table_fqn, m.column_name)
    for d in ir.dimensions:
        _register(d.table_fqn, d.column_name)
    for f in ir.filters:
        if not f.is_raw_sql:
            _register(f.table_fqn, f.column_name)
    if ir.time_filter:
        _register(ir.time_filter.table_fqn, ir.time_filter.column_name)

    if not table_cols:
        return ir

    def _parse_fqn(fqn: str) -> tuple[str, str] | None:
        parts = fqn.rsplit(".", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else None

    async def _check_table(fqn: str, cols: set[str]) -> tuple[str, set[str], list[str]]:
        parsed = _parse_fqn(fqn)
        if not parsed:
            return fqn, cols, []
        schema, table = parsed
        logger.info("ir_utils | col_check | {}.{} | cols={}", schema, table, list(cols))
        try:
            await get_table_columns(schema, table, list(cols))
            all_cached = get_schema_cols(schema, table) or []
            all_names = [r[0] for r in all_cached]
            valid = {n for n in all_names if n in cols}
            return fqn, valid, all_names
        except Exception:
            return fqn, cols, []

    _sem = asyncio.Semaphore(3)

    async def _check_table_guarded(fqn: str, cols: set[str]) -> tuple:
        async with _sem:
            return await _check_table(fqn, cols)

    results = await asyncio.gather(*[_check_table_guarded(fqn, cols) for fqn, cols in table_cols.items()])

    valid_pairs: set[tuple[str, str]] = {
        (fqn, col) for fqn, valid_cols, _ in results for col in valid_cols
    }
    all_table_cols: dict[str, list[str]] = {
        fqn: all_names for fqn, _, all_names in results
    }

    def _fuzzy_remap(fqn: str, col: str) -> str | None:
        candidates = all_table_cols.get(fqn, [])
        if not candidates:
            return None
        try:
            from rapidfuzz import fuzz, process
            match = process.extractOne(col, candidates, scorer=fuzz.WRatio)
            if match and match[1] >= 75:
                logger.info("ir_utils | remap | {}.{} → {} score={:.0f}", fqn, col, match[0], match[1])
                return match[0]
        except Exception:
            pass
        return None

    def _resolve(fqn: str, col: str) -> tuple[str, str]:
        if not fqn or not col or fqn not in table_cols:
            return col, "keep"
        if (fqn, col) in valid_pairs:
            return col, "keep"
        best = _fuzzy_remap(fqn, col)
        if best:
            return best, "remap"
        return col, "drop"

    changes: list[str] = []

    def _fix(obj, col_attr: str = "column_name"):
        fqn = getattr(obj, "table_fqn", "")
        col = getattr(obj, col_attr, "")
        resolved, action = _resolve(fqn, col)
        if action == "remap":
            changes.append(f"{fqn}.{col} → {resolved}")
            return obj.model_copy(update={col_attr: resolved}), True
        if action == "drop":
            changes.append(f"{fqn}.{col} [dropped]")
            return obj, False
        return obj, True

    new_measures, new_dimensions, new_filters = [], [], []
    for m in ir.measures:
        fixed, keep = _fix(m)
        if keep:
            new_measures.append(fixed)
    for d in ir.dimensions:
        fixed, keep = _fix(d)
        if keep:
            new_dimensions.append(fixed)
    for f in ir.filters:
        if f.is_raw_sql:
            new_filters.append(f)
            continue
        fixed, keep = _fix(f)
        if keep:
            new_filters.append(fixed)

    new_time_filter = ir.time_filter
    if ir.time_filter:
        fixed_tf, keep_tf = _fix(ir.time_filter)
        new_time_filter = fixed_tf if keep_tf else None

    if not changes:
        return ir

    remapped = [c for c in changes if "→" in c]
    dropped = [c for c in changes if "dropped" in c]
    logger.warning(
        "ir_utils | col_validation | thread={} | remapped={} | dropped={} | detail={}",
        thread_id, len(remapped), len(dropped), changes,
    )
    return ir.model_copy(update={
        "measures": new_measures,
        "dimensions": new_dimensions,
        "filters": new_filters,
        "time_filter": new_time_filter,
    })


# ── validate_and_fix_join_clauses ─────────────────────────────────────────────

# Matches fully-qualified column refs: schema.table.column
_FQN_COL_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)")


async def validate_and_fix_join_clauses(ir: SemanticIR, thread_id: str) -> SemanticIR:
    """Validate and fuzzy-fix column names inside join clause strings.

    Handles:
      - JoinPath join_clauses: "schema.table.col = schema.table.col"
      - JOINS_TO bare clauses: same fully-qualified format after _qualify_join_clause

    Uses the Redis schema_cols cache; on cache miss calls Redshift information_schema.
    Rewrites ir.join_clauses and all ir.candidate_join_paths[*].join_clauses in-place.
    """
    # Collect all unique table FQNs referenced across join clauses
    all_clauses: list[str] = list(ir.join_clauses or [])
    if ir.candidate_join_paths:
        for path in ir.candidate_join_paths:
            all_clauses.extend(path.get("join_clauses") or [])

    fqns_needed: set[str] = set()
    for clause in all_clauses:
        for match in _FQN_COL_RE.finditer(clause):
            schema, table = match.group(1), match.group(2)
            fqns_needed.add(f"{schema}.{table}")

    if not fqns_needed:
        return ir

    # Load schema cache for all referenced tables.
    # Uses fetch_table_schema which always returns ALL columns and writes to Redis.
    # get_table_columns(schema, table, []) returns [] immediately — cannot be used here.
    async def _load(fqn: str) -> tuple[str, list[str]]:
        parts = fqn.rsplit(".", 1)
        if len(parts) != 2:
            return fqn, []
        schema, table = parts
        from app.services.agents.redshift_client import fetch_table_schema
        try:
            all_cols = await fetch_table_schema(schema, table)
            return fqn, [r[0] for r in all_cols]
        except Exception:
            return fqn, []

    _join_sem = asyncio.Semaphore(3)

    async def _load_guarded(fqn: str) -> tuple:
        async with _join_sem:
            return await _load(fqn)

    schema_results = await asyncio.gather(*[_load_guarded(fqn) for fqn in fqns_needed])
    col_map: dict[str, list[str]] = dict(schema_results)

    changes: list[str] = []

    def _fix_col(schema: str, table: str, col: str) -> str | None:
        """Return corrected column name, or None if confirmed absent with no fuzzy match.

        None means the column is CONFIRMED not to exist in Redshift (we have real schema
        data for this table but the column isn't there and no fuzzy match scored >= 75).
        Returning None marks the entire clause as invalid.
        """
        fqn = f"{schema}.{table}"
        real_cols = col_map.get(fqn, [])
        if not real_cols:
            # Table not in cache — can't confirm absence; pass through (best-effort)
            return col
        if col in real_cols:
            return col
        # Column confirmed absent — try fuzzy remap
        try:
            from rapidfuzz import fuzz, process
            match = process.extractOne(col, real_cols, scorer=fuzz.WRatio)
            if match and match[1] >= 75:
                changes.append(f"{fqn}.{col} → {match[0]} (join clause, score={match[1]:.0f})")
                return match[0]
        except Exception:
            pass
        # Confirmed absent, no fuzzy match — mark as invalid
        logger.warning("ir_utils | join_col_invalid | {}.{} | confirmed absent in Redshift schema", fqn, col)
        return None

    def _fix_clause(clause: str) -> str | None:
        """Return fixed clause, or None if any column is confirmed invalid."""
        invalid = False

        def _replace(m: re.Match) -> str:
            nonlocal invalid
            schema, table, col = m.group(1), m.group(2), m.group(3)
            result = _fix_col(schema, table, col)
            if result is None:
                invalid = True
                return f"{schema}.{table}.{col}"  # placeholder; clause will be discarded
            return f"{schema}.{table}.{result}"

        fixed = _FQN_COL_RE.sub(_replace, clause)
        return None if invalid else fixed

    # Fix candidate paths first — build a lookup of valid paths by (from_fqn, to_fqn)
    valid_candidate_map: dict[tuple[str, str], list[dict]] = {}
    new_candidate_paths = None
    if ir.candidate_join_paths:
        new_candidate_paths = []
        for path in ir.candidate_join_paths:
            raw_clauses = path.get("join_clauses") or []
            fixed_clauses = [_fix_clause(c) for c in raw_clauses]
            if any(c is None for c in fixed_clauses):
                logger.warning(
                    "ir_utils | join_path_dropped | tier={} | from={} to={} | invalid column in clause",
                    path.get("tier"), path.get("from_fqn"), path.get("to_fqn"),
                )
                continue  # drop this path entirely
            valid_path = {**path, "join_clauses": [c for c in fixed_clauses if c]}
            new_candidate_paths.append(valid_path)
            key = (path.get("from_fqn", ""), path.get("to_fqn", ""))
            valid_candidate_map.setdefault(key, []).append(valid_path)

    # Fix primary join clauses; fall back to valid candidates when a clause is invalid
    new_join_clauses: list[str] = []
    for clause in (ir.join_clauses or []):
        fixed = _fix_clause(clause)
        if fixed is not None:
            new_join_clauses.append(fixed)
            continue

        # Primary clause is invalid — try to find a valid fallback from candidates
        # Extract the two table FQNs from the clause
        tables_in_clause = list(dict.fromkeys(
            f"{m.group(1)}.{m.group(2)}" for m in _FQN_COL_RE.finditer(clause)
        ))
        fallback: str | None = None
        if len(tables_in_clause) >= 2:
            # Try both directions
            for key in [(tables_in_clause[0], tables_in_clause[1]),
                        (tables_in_clause[1], tables_in_clause[0])]:
                alts = valid_candidate_map.get(key, [])
                for alt in alts:
                    alt_clauses = alt.get("join_clauses") or []
                    if alt_clauses:
                        fallback_clause = alt_clauses[0]
                        logger.info(
                            "ir_utils | primary_clause_fallback | invalid_clause={} | using tier={} | new_clause={}",
                            clause, alt.get("tier"), fallback_clause,
                        )
                        fallback = fallback_clause
                        break
                if fallback:
                    break
        if fallback:
            new_join_clauses.append(fallback)
        else:
            logger.warning(
                "ir_utils | primary_clause_no_fallback | clause={} | using sentinel (empty join)",
                clause,
            )
            new_join_clauses.append("")  # sentinel — SQL generator handles this

    if not changes and new_join_clauses == list(ir.join_clauses or []):
        return ir

    if changes:
        logger.warning(
            "ir_utils | join_clause_validation | thread={} | fixes={} | detail={}",
            thread_id, len(changes), changes,
        )
    return ir.model_copy(update={
        "join_clauses": new_join_clauses,
        "candidate_join_paths": new_candidate_paths if new_candidate_paths is not None else ir.candidate_join_paths,
    })

"""Column and join clause validation against Neo4j-sourced data.

Replaces Redshift-based ir_utils.strip_hallucinated_columns and
ir_utils.validate_and_fix_join_clauses with _column_lookup validation.

3-layer hallucination defense:
  Layer 1: Prompt constraint (CRITICAL header in intent_resolver)
  Layer 2: _validate_identifiers() in intent_resolver (early warning + table check)
  Layer 3: strip_hallucinated_columns() here (actual correction + fuzzy remap)
"""

from __future__ import annotations

import re as _re

from app.core.logger import logger
from app.services.agents.semantic_ir import SemanticIR

_FQN_COL_RE = _re.compile(
    r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)"
)

_UUID_SUFFIXES = ("_uuid", "_guid", "_uid")


def _is_uuid_col(col_name: str) -> bool:
    """Return True for UUID/GUID columns that are internal row identifiers.

    These columns are unique per row and will ALWAYS produce 0 rows when used
    as join keys across tables. Strip them from join candidates before the LLM sees them.
    """
    n = col_name.lower()
    return n == "uuid" or any(n.endswith(s) for s in _UUID_SUFFIXES)


async def strip_hallucinated_columns(
    ir: SemanticIR,
    thread_id: str,
    semantic_context: dict | None = None,
) -> SemanticIR:
    """Validate and remap column references against Neo4j column data.

    When semantic_context provided:
    - Validates table_fqn against known anchor tables
    - Validates col_name against all columns for that table
    - Fuzzy remaps via rapidfuzz WRatio >= 75 if column name slightly off
    - Drops if table hallucinated or no fuzzy match

    Safety: if ALL measures dropped, restores original (wrong col beats empty SQL).
    """
    if not semantic_context:
        return await _strip_via_redshift_legacy(ir, thread_id)

    lookup: dict[tuple, bool] = {}
    for col in (semantic_context.get("columns") or []):
        tfqn = col.get("table_fqn", "")
        cname = col.get("name", "")
        if tfqn and cname:
            lookup[(tfqn, cname)] = True

    # Also check _column_lookup for full coverage (includes non-display columns)
    for (tfqn, cname) in (semantic_context.get("_column_lookup") or {}):
        lookup[(tfqn, cname)] = True

    valid_tables = {t["fqn"] for t in (semantic_context.get("tables") or []) if t.get("fqn")}
    cols_by_table: dict[str, set[str]] = {}
    for (tfqn, cname) in lookup:
        cols_by_table.setdefault(tfqn, set()).add(cname)

    original_measures = list(ir.measures)
    dropped_cols: list[str] = []  # track dropped columns to inject as INVALID COLUMNS in SQL gen

    def _validate_col_ref(table_fqn: str, col_name: str) -> str | None:
        """Return corrected col_name or None to drop."""
        if not table_fqn or table_fqn not in valid_tables:
            logger.warning("ir_validation | hallucinated_table | {} | thread={}", table_fqn, thread_id)
            dropped_cols.append(f"{table_fqn}.{col_name}")
            return None
        valid_cols = cols_by_table.get(table_fqn, set())
        if col_name in valid_cols:
            return col_name
        # Fuzzy remap
        if valid_cols:
            try:
                from rapidfuzz.process import extractOne
                from rapidfuzz.fuzz import WRatio
                best = extractOne(col_name, valid_cols, scorer=WRatio)
                if best and best[1] >= 75:
                    logger.info("ir_validation | fuzzy_remap | {}.{} → {} | score={} | thread={}",
                                table_fqn, col_name, best[0], best[1], thread_id)
                    return best[0]
            except ImportError:
                pass
        logger.warning("ir_validation | hallucinated_col | {}.{} | thread={}", table_fqn, col_name, thread_id)
        dropped_cols.append(f"{table_fqn}.{col_name}")
        return None

    # Validate measures
    valid_measures = []
    for m in ir.measures:
        corrected = _validate_col_ref(m.table_fqn, m.column_name)
        if corrected:
            valid_measures.append(m.model_copy(update={"column_name": corrected}))
    if not valid_measures and original_measures:
        logger.error("ir_validation | all_measures_dropped | restoring_original | thread={}", thread_id)
        valid_measures = original_measures

    # Validate dimensions
    valid_dims = []
    for d in ir.dimensions:
        corrected = _validate_col_ref(d.table_fqn, d.column_name)
        if corrected:
            valid_dims.append(d.model_copy(update={"column_name": corrected}))

    # Validate filter column names (keep filter even if col not found — use raw value)
    valid_filters = []
    for f in ir.filters:
        corrected = _validate_col_ref(f.table_fqn, f.column_name)
        if corrected:
            valid_filters.append(f.model_copy(update={"column_name": corrected}))
        # else: silently drop the filter (don't crash on hallucinated filter column)

    return ir.model_copy(update={
        "measures": valid_measures,
        "dimensions": valid_dims,
        "filters": valid_filters,
        "hallucinated_columns": dropped_cols,
    })


async def validate_and_fix_join_clauses(
    ir: SemanticIR,
    thread_id: str,
    semantic_context: dict | None = None,
) -> SemanticIR:
    """Validate column names in join clause strings against Neo4j column data.

    JoinPath clauses come from pre-validated Neo4j data (declared YAML FKs).
    In practice almost all clauses pass validation — this is a safety net.
    """
    if not semantic_context:
        return await _fix_joins_via_redshift_legacy(ir, thread_id)

    schema_by_table: dict[str, set[str]] = {}
    for (tfqn, cname) in (semantic_context.get("_column_lookup") or {}):
        schema_by_table.setdefault(tfqn, set()).add(cname)

    if not schema_by_table:
        return ir

    def _fix_clause(clause: str) -> str | None:
        matches = list(_FQN_COL_RE.finditer(clause))
        if len(matches) < 2:
            return clause
        # Drop clauses where either side is a UUID column — they are unique per row
        # and will always produce 0 rows when used as join keys.
        for m in matches:
            if _is_uuid_col(m.group(3)):
                logger.warning(
                    "ir_validation | uuid_join_rejected | col={} | clause={} | thread={}",
                    m.group(3), clause[:80], thread_id,
                )
                return None
        fixed_clause = clause
        for m in matches:
            schema, table, col = m.group(1), m.group(2), m.group(3)
            fqn = f"{schema}.{table}"
            valid_cols = schema_by_table.get(fqn)
            if valid_cols is None:
                continue  # table not in cache — assume valid
            if col not in valid_cols:
                try:
                    from rapidfuzz.process import extractOne
                    from rapidfuzz.fuzz import WRatio
                    best = extractOne(col, valid_cols, scorer=WRatio)
                    if best and best[1] >= 75:
                        fixed_clause = fixed_clause.replace(m.group(0), f"{fqn}.{best[0]}")
                        logger.info("ir_validation | join_clause_remap | {}.{} → {} | thread={}",
                                    fqn, col, best[0], thread_id)
                    else:
                        logger.warning("ir_validation | join_clause_invalid_col | {}.{} | thread={}",
                                       fqn, col, thread_id)
                        return None
                except ImportError:
                    pass
        return fixed_clause

    # Fix primary join clauses
    fixed_join_clauses = []
    for clause in (ir.join_clauses or []):
        result = _fix_clause(clause)
        fixed_join_clauses.append(result if result else "")  # empty = unresolved, handled by SQL LLM

    # Fix candidate join paths
    fixed_candidates = []
    for path in (ir.candidate_join_paths or []):
        fixed_clauses = []
        for clause in (path.get("join_clauses") or []):
            result = _fix_clause(clause)
            if result:
                fixed_clauses.append(result)
        if fixed_clauses:
            fixed_candidates.append({**path, "join_clauses": fixed_clauses})

    return ir.model_copy(update={
        "join_clauses": fixed_join_clauses,
        "candidate_join_paths": fixed_candidates or ir.candidate_join_paths,
    })


async def _strip_via_redshift_legacy(ir: SemanticIR, thread_id: str) -> SemanticIR:
    """Legacy Redshift-based validation — kept for backward compat during migration."""
    try:
        from app.services.agents.ir_utils import strip_hallucinated_columns as _legacy
        return await _legacy(ir, thread_id)
    except Exception as e:
        logger.warning("ir_validation | legacy_strip failed | thread={} | error={}", thread_id, e)
        return ir


async def _fix_joins_via_redshift_legacy(ir: SemanticIR, thread_id: str) -> SemanticIR:
    """Legacy Redshift-based join validation — kept for backward compat."""
    try:
        from app.services.agents.ir_utils import validate_and_fix_join_clauses as _legacy
        return await _legacy(ir, thread_id)
    except Exception as e:
        logger.warning("ir_validation | legacy_fix_joins failed | thread={} | error={}", thread_id, e)
        return ir

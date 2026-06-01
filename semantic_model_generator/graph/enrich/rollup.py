"""
Rollup relationship detection — schema-driven, no LLM, no name patterns.

Detects pre-aggregated / time-windowed table variants by structural analysis
of column overlap and temporal grain columns in the Neo4j graph.

Creates ROLLUP_OF edges and writes is_subquery_anchor on base tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)
_NOW = lambda: datetime.now(timezone.utc).isoformat()

_GRAIN_WINDOW_MAP = {
    "week":    ("weekly",           7),
    "month":   ("monthly",          30),
    "quarter": ("quarter_to_date",  90),
    "year":    ("year_to_date",     365),
}

_MIN_CONFIDENCE = 0.70


def detect_rollup_candidates_from_graph(loader) -> list[dict]:
    """
    Schema-driven rollup detection via Neo4j Cypher structural analysis.
    Requires temporal_grain to be set on Column nodes (run after ENRICH step).

    Detection criteria:
    - Candidate row_count <= base row_count * 1.1
    - Column overlap (shared / base) >= 0.60
    - Candidate has a temporal grain column (week/month/quarter/year) absent in base
    - No JOINS_TO edge from candidate to base (not a parent-child FK pair)

    Returns list of {rollup_fqn, base_fqn, window_type, window_days, confidence}
    where confidence = shared_cols / base_col_count (column overlap ratio).
    """
    rows = loader._run("""
        MATCH (base:Table)-[:HAS_COLUMN]->(bc:Column)
        WHERE base.row_count > 0
        WITH base, collect(bc.name) AS base_cols

        MATCH (cand:Table)-[:HAS_COLUMN]->(cc:Column)
        WHERE cand.fqn <> base.fqn
          AND cand.row_count > 0
          AND cand.row_count <= base.row_count * 1.1
        WITH base, cand, base_cols, collect(cc.name) AS cand_cols
        WITH base, cand, base_cols, cand_cols,
             size([c IN cand_cols WHERE c IN base_cols]) AS shared
        WHERE shared >= toInteger(size(base_cols) * 0.6)
          AND size(cand_cols) <= size(base_cols) + 4

        MATCH (cand)-[:HAS_COLUMN]->(gc:Column)
        WHERE gc.temporal_grain IN ['week','month','quarter','year']
          AND NOT gc.name IN base_cols
        WITH base, cand, base_cols, cand_cols, shared, gc
        WHERE NOT EXISTS { MATCH (cand)-[:JOINS_TO]->(base) }

        RETURN base.fqn AS base_fqn, cand.fqn AS rollup_fqn,
               gc.name AS grain_col, gc.temporal_grain AS grain,
               shared AS shared_cols, size(base_cols) AS base_col_count,
               base.row_count AS base_rows, cand.row_count AS rollup_rows
        ORDER BY base.fqn
        LIMIT 200
    """)

    candidates = []
    seen: set[str] = set()
    for r in rows:
        key = f"{r['rollup_fqn']}|{r['base_fqn']}"
        if key in seen:
            continue
        seen.add(key)

        base_col_count = r.get("base_col_count") or 1
        shared = r.get("shared_cols") or 0
        confidence = round(shared / base_col_count, 3)

        if confidence < _MIN_CONFIDENCE:
            continue

        window_type, window_days = _GRAIN_WINDOW_MAP.get(r["grain"], ("unknown", None))
        candidates.append({
            "rollup_fqn":  r["rollup_fqn"],
            "base_fqn":    r["base_fqn"],
            "grain_col":   r.get("grain_col"),
            "window_type": window_type,
            "window_days": window_days,
            "confidence":  confidence,
        })

    log.info("Schema-driven rollup detection: %d candidates (min_confidence=%.2f).",
             len(candidates), _MIN_CONFIDENCE)
    return candidates

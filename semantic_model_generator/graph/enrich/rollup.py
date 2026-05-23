"""
Rollup relationship detection and is_subquery_anchor enrichment.

Detects time-windowed table variants (e.g. transfer_in_last_7, balance_mtd)
and creates ROLLUP_OF edges linking them to their base tables.
Also writes is_rollup / rollup_base_fqn / rollup_window_days on rollup tables.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..enrich.llm_enricher import _invoke, _parse_json_response

log = logging.getLogger(__name__)
_NOW = lambda: datetime.now(timezone.utc).isoformat()

# Regex patterns that identify rollup/time-windowed table suffixes
_ROLLUP_PATTERNS = [
    (re.compile(r"_last[_]?(\d+)[d]?$", re.I),         "trailing_N_days"),
    (re.compile(r"_(\d+)[d]?_rolling$", re.I),          "trailing_N_days"),
    (re.compile(r"_mtd$", re.I),                         "month_to_date"),
    (re.compile(r"_ytd$", re.I),                         "year_to_date"),
    (re.compile(r"_qtd$", re.I),                         "quarter_to_date"),
    (re.compile(r"_weekly$", re.I),                      "weekly"),
    (re.compile(r"_monthly$", re.I),                     "monthly"),
    (re.compile(r"_daily$", re.I),                       "daily"),
    (re.compile(r"_7d(ay)?s?$", re.I),                   "trailing_7_days"),
    (re.compile(r"_30d(ay)?s?$", re.I),                  "trailing_30_days"),
    (re.compile(r"_90d(ay)?s?$", re.I),                  "trailing_90_days"),
]

_WINDOW_DAYS = {
    "trailing_N_days":  None,   # extracted from suffix
    "month_to_date":    30,
    "year_to_date":     365,
    "quarter_to_date":  90,
    "weekly":           7,
    "monthly":          30,
    "daily":            1,
    "trailing_7_days":  7,
    "trailing_30_days": 30,
    "trailing_90_days": 90,
}

_MIN_CONFIDENCE = 0.80

_GRAIN_WINDOW_MAP = {
    "week":    ("weekly",           7),
    "month":   ("monthly",          30),
    "quarter": ("quarter_to_date",  90),
    "year":    ("year_to_date",     365),
}


def detect_rollup_candidates_from_graph(loader) -> list[dict]:
    """
    Schema-driven rollup detection via Neo4j Cypher structural analysis.
    Requires temporal_grain to be set on Column nodes (run after ENRICH step).

    Detection criteria:
    - Candidate row_count <= base row_count * 1.1 (aggregated)
    - Column overlap >= 60% of base columns
    - Candidate has a temporal grain column (week/month/quarter/year) absent in base
    - No FK cand→base (not a parent-child pair)

    Returns list of {rollup_fqn, base_fqn, window_type, window_days, confidence}
    with the same shape as detect_rollup_candidates().
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
        window_type, window_days = _GRAIN_WINDOW_MAP.get(r["grain"], ("unknown", None))
        candidates.append({
            "rollup_fqn":  r["rollup_fqn"],
            "base_fqn":    r["base_fqn"],
            "grain_col":   r.get("grain_col"),
            "window_type": window_type,
            "window_days": window_days,
            "confidence":  0.90,
        })

    log.info("Schema-driven rollup detection: %d candidates found.", len(candidates))
    return candidates


def _strip_rollup_suffix(name: str) -> str:
    """Remove the rollup suffix to get the candidate base table name."""
    for pattern, _ in _ROLLUP_PATTERNS:
        stripped = pattern.sub("", name)
        if stripped != name:
            return stripped
    return name


def detect_rollup_candidates(table_names: list[str]) -> list[dict]:
    """
    Pure heuristic pass — no LLM.
    Returns list of {rollup_fqn, base_candidate_name, window_type, window_days, confidence}
    for pairs where a rollup-suffixed table has a matching base table in the set.
    """
    name_set = set(table_names)
    # Build short_name → fqn mapping
    short_to_fqn: dict[str, str] = {}
    for fqn in table_names:
        short = fqn.split(".")[-1]
        short_to_fqn[short] = fqn

    candidates = []
    for fqn in table_names:
        short = fqn.split(".")[-1]
        schema = fqn.rsplit(".", 1)[0]

        for pattern, window_type in _ROLLUP_PATTERNS:
            m = pattern.search(short)
            if not m:
                continue

            base_short = pattern.sub("", short)
            base_fqn = short_to_fqn.get(base_short) or f"{schema}.{base_short}"

            if base_fqn not in name_set and f"{schema}.{base_short}" not in name_set:
                continue

            # Extract N from trailing_N_days
            window_days = _WINDOW_DAYS.get(window_type)
            if window_type == "trailing_N_days" and m.group(1):
                try:
                    window_days = int(m.group(1))
                except ValueError:
                    pass

            candidates.append({
                "rollup_fqn":        fqn,
                "base_fqn":          base_fqn,
                "window_type":       window_type,
                "window_days":       window_days,
                "confidence":        0.90,
            })
            break  # one match per table

    return candidates


_ROLLUP_LLM_PROMPT = """You are validating rollup/time-windowed table relationships in a data warehouse.

For each candidate pair below, determine:
1. Is the "rollup_table" genuinely a time-windowed or pre-aggregated variant of "base_table"?
2. Assign confidence between 0.0 and 1.0.

Candidate pairs:
{pairs_json}

Return a JSON array — one object per candidate:
{{
  "rollup_fqn": "...",
  "base_fqn": "...",
  "window_type": "...",
  "window_days": <int or null>,
  "confidence": 0.92,
  "confirmed": true
}}

Return ONLY valid JSON array. No markdown."""


def validate_rollup_with_llm(
    candidates: list[dict],
    table_descriptions: dict[str, str],
    client,
    model_arn: str,
) -> list[dict]:
    """
    Use LLM to validate heuristic rollup candidates.
    Returns only pairs with confidence >= _MIN_CONFIDENCE and confirmed=true.
    """
    if not candidates:
        return []

    enriched = []
    for c in candidates:
        enriched.append({
            **c,
            "rollup_description": table_descriptions.get(c["rollup_fqn"], ""),
            "base_description":   table_descriptions.get(c["base_fqn"], ""),
        })

    prompt = _ROLLUP_LLM_PROMPT.format(
        pairs_json=json.dumps(enriched, indent=2, default=str)
    )
    try:
        raw = _invoke(client, model_arn, [{"role": "user", "content": prompt}], max_tokens=3000)
        parsed = _parse_json_response(raw)
        return [
            item for item in parsed
            if item.get("confirmed") is True
            and float(item.get("confidence", 0)) >= _MIN_CONFIDENCE
        ]
    except Exception as e:
        log.error("Rollup LLM validation failed: %s — returning heuristic candidates", e)
        return [c for c in candidates if c["confidence"] >= _MIN_CONFIDENCE]

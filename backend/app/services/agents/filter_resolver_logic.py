"""Filter value resolution logic — pure functions, no I/O.

The tiered resolution pipeline:
  Tier 1 Combined: aliases → exact match on filter_values → fuzzy (rapidfuzz WRatio)
  Tier 2: Redshift DISTINCT probe — only when filter_values is empty (not in this file)
  Tier 3: Temporal expression parsing
  Tier 5: LLM disambiguation (not in this file)
"""

from __future__ import annotations


def resolve_tier1_combined(
    user_value: str,
    filter_values: list[str],
    value_aliases: dict[str, str] | None,
) -> tuple[str | None, float, list[str]]:
    """Single-pass: aliases → exact (case-insensitive) → fuzzy (rapidfuzz WRatio).

    filter_values should be the Redshift-probed distinct values written by context_fetcher
    enrichment — NOT Neo4j's sample_values (which are partial and truncated).

    Returns (resolved_value, score, ambiguous_candidates).
    - resolved_value + score >= 85 + empty candidates → confident match
    - resolved_value + 70 <= score < 85 → low-confidence match
    - None + candidates → ambiguous (multiple ≥85 matches)
    - None + empty candidates → no match
    """
    user_lower = user_value.lower()

    if isinstance(value_aliases, dict):
        for alias, canonical in value_aliases.items():
            # Reverse lookup: user said the human label → return the DB code
            if canonical.lower() == user_lower:
                return alias, 100.0, []
            # Forward lookup: user already said the DB code → return it (not the human name)
            if alias.lower() == user_lower:
                return alias, 100.0, []

    for v in (filter_values or []):
        if str(v).lower() == user_lower:
            return v, 100.0, []

    if not filter_values or len(filter_values) > 500:
        return None, 0.0, []

    try:
        from rapidfuzz import fuzz, process
        matches = process.extract(user_value, filter_values, scorer=fuzz.WRatio, limit=5)
    except ImportError:
        return None, 0.0, []

    if not matches:
        return None, 0.0, []

    above_85 = [(m[0], m[1]) for m in matches if m[1] >= 85]
    top_val, top_score = matches[0][0], matches[0][1]

    if top_score >= 85 and len(above_85) == 1:
        return top_val, top_score, []
    if 70 <= top_score < 85:
        return top_val, top_score, []
    if len(above_85) > 1:
        return None, top_score, [m[0] for m in above_85]
    return None, top_score, []


def resolve_tier1_exact(
    user_value: str,
    sample_values: list[str],
    value_aliases: dict[str, str] | None,
) -> str | None:
    """Tier 1: exact match against aliases and sample_values (legacy — kept for compatibility)."""
    if value_aliases and isinstance(value_aliases, dict):
        for alias, canonical in value_aliases.items():
            if alias.lower() == user_value.lower():
                return canonical

    user_lower = user_value.lower()
    for sv in (sample_values or []):
        if sv.lower() == user_lower:
            return sv

    return None


def resolve_tier2_fuzzy(
    user_value: str,
    sample_values: list[str],
) -> tuple[str | None, float, list[str]]:
    """Tier 2: fuzzy match against sample_values (legacy — kept for compatibility)."""
    if not sample_values:
        return None, 0.0, []

    if len(sample_values) > 500:
        return None, 0.0, []

    try:
        from rapidfuzz import fuzz, process
        matches = process.extract(user_value, sample_values, scorer=fuzz.WRatio, limit=5)
    except ImportError:
        return None, 0.0, []

    if not matches:
        return None, 0.0, []

    above_85 = [(m[0], m[1]) for m in matches if m[1] >= 85]
    top_match, top_score = matches[0][0], matches[0][1]

    if top_score >= 85 and len(above_85) == 1:
        return top_match, top_score, []

    if 70 <= top_score < 85:
        return top_match, top_score, []

    if len(above_85) > 1:
        candidates = [m[0] for m in above_85]
        return None, top_score, candidates

    return None, top_score, []


def resolve_tier3_temporal(user_value: str) -> dict | None:
    """Sync pre-check for unambiguously literal temporal expressions.

    Handles ONLY: 'today', 'yesterday', exact ISO date 'YYYY-MM-DD', and
    exact ISO range 'YYYY-MM-DD to YYYY-MM-DD'. All other expressions —
    including any natural language phrase like 'last 2 months', 'this month
    vs last month', 'Q3 2024', etc. — must go through _tier35_temporal_llm
    (Haiku) which handles arbitrary temporal language reliably.

    Returns {operator, value, is_raw_sql} or None.
    """
    import re
    v = user_value.strip().lower()

    if v == "today":
        return {"operator": "=", "value": "CURRENT_DATE", "is_raw_sql": True}

    if v == "yesterday":
        return {"operator": "=", "value": "DATEADD(day, -1, CURRENT_DATE)", "is_raw_sql": True}

    if re.match(r"\d{4}-\d{2}-\d{2}$", v):
        return {"operator": "=", "value": v}

    if re.match(r"\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}$", v):
        parts = v.split(" to ")
        return {"operator": "BETWEEN", "value": parts}

    return None


def build_redshift_probe_sql(table_fqn: str, col_name: str, user_value: str) -> str:
    """Build a probe SQL — DISTINCT values, alphabetically sorted, up to 100."""
    return (
        f'SELECT DISTINCT CAST("{col_name}" AS VARCHAR) AS val '
        f"FROM {table_fqn} "
        f'WHERE "{col_name}" IS NOT NULL '
        f"ORDER BY 1 LIMIT 100"
    )


def build_redshift_probe_params(user_value: str) -> list:
    return []


def is_time_sensitive_sql(sql: str) -> bool:
    """Returns True if the SQL contains time-sensitive functions — skip caching."""
    sql_upper = sql.upper()
    return any(fn in sql_upper for fn in ["GETDATE()", "SYSDATE", "CURRENT_DATE", "CURRENT_TIMESTAMP"])

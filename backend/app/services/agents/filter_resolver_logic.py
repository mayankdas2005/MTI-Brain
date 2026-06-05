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
    """Sync pre-check for standard temporal expressions — deterministic, no LLM.

    Handles: 'today', 'yesterday', exact ISO date/range, and the complete set
    of standard past/forward timeframe slugs emitted by the filter_specialist.
    Any non-matching expression falls through to _tier35_temporal_llm (Haiku).

    Returns {operator, value, is_raw_sql} or None.
    """
    import re
    v = user_value.strip().lower().replace(" ", "_")

    if v == "today":
        return {"operator": "=", "value": "CURRENT_DATE", "is_raw_sql": True}

    if v == "yesterday":
        return {"operator": "=", "value": "DATEADD(day, -1, CURRENT_DATE)", "is_raw_sql": True}

    if re.match(r"\d{4}-\d{2}-\d{2}$", v):
        return {"operator": "=", "value": v}

    if re.match(r"\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2}$", v) or re.match(r"\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}$", user_value.strip()):
        parts = user_value.strip().replace(" to ", " TO ").split(" TO ")
        if len(parts) == 2:
            return {"operator": "BETWEEN", "value": [p.strip() for p in parts]}

    # Standard past-window slugs
    _PAST = {
        "last_7_days":    ["DATEADD(day,-7,CURRENT_DATE)", "CURRENT_DATE"],
        "last_14_days":   ["DATEADD(day,-14,CURRENT_DATE)", "CURRENT_DATE"],
        "last_30_days":   ["DATEADD(day,-30,CURRENT_DATE)", "CURRENT_DATE"],
        "last_60_days":   ["DATEADD(day,-60,CURRENT_DATE)", "CURRENT_DATE"],
        "last_90_days":   ["DATEADD(day,-90,CURRENT_DATE)", "CURRENT_DATE"],
        "last_7_days":    ["DATEADD(day,-7,CURRENT_DATE)", "CURRENT_DATE"],
        "last_week":      ["DATEADD(week,-1,CURRENT_DATE)", "CURRENT_DATE"],
        "last_2_weeks":   ["DATEADD(week,-2,CURRENT_DATE)", "CURRENT_DATE"],
        "last_month":     ["DATE_TRUNC('month',DATEADD(month,-1,CURRENT_DATE))",
                           "DATEADD(day,-1,DATE_TRUNC('month',CURRENT_DATE))"],
        "last_3_months":  ["DATEADD(month,-3,CURRENT_DATE)", "CURRENT_DATE"],
        "last_6_months":  ["DATEADD(month,-6,CURRENT_DATE)", "CURRENT_DATE"],
        "last_12_months": ["DATEADD(month,-12,CURRENT_DATE)", "CURRENT_DATE"],
        "last_year":      ["DATEADD(year,-1,CURRENT_DATE)", "CURRENT_DATE"],
        "last_quarter":   ["DATE_TRUNC('quarter',DATEADD(quarter,-1,CURRENT_DATE))",
                           "DATEADD(day,-1,DATE_TRUNC('quarter',CURRENT_DATE))"],
        "this_month":     ["DATE_TRUNC('month',CURRENT_DATE)", "CURRENT_DATE"],
        "this_quarter":   ["DATE_TRUNC('quarter',CURRENT_DATE)", "CURRENT_DATE"],
        "this_year":      ["DATE_TRUNC('year',CURRENT_DATE)", "CURRENT_DATE"],
        "ytd":            ["DATE_TRUNC('year',CURRENT_DATE)", "CURRENT_DATE"],
        "mtd":            ["DATE_TRUNC('month',CURRENT_DATE)", "CURRENT_DATE"],
        "qtd":            ["DATE_TRUNC('quarter',CURRENT_DATE)", "CURRENT_DATE"],
    }

    # Standard forward-window slugs
    _FORWARD = {
        "next_7_days":    ["CURRENT_DATE", "DATEADD(day,7,CURRENT_DATE)"],
        "next_14_days":   ["CURRENT_DATE", "DATEADD(day,14,CURRENT_DATE)"],
        "next_30_days":   ["CURRENT_DATE", "DATEADD(day,30,CURRENT_DATE)"],
        "next_4_weeks":   ["CURRENT_DATE", "DATEADD(week,4,CURRENT_DATE)"],
        "next_60_days":   ["CURRENT_DATE", "DATEADD(day,60,CURRENT_DATE)"],
        "next_90_days":   ["CURRENT_DATE", "DATEADD(day,90,CURRENT_DATE)"],
        "next_3_months":  ["CURRENT_DATE", "DATEADD(month,3,CURRENT_DATE)"],
        "next_6_months":  ["CURRENT_DATE", "DATEADD(month,6,CURRENT_DATE)"],
        "next_12_months": ["CURRENT_DATE", "DATEADD(month,12,CURRENT_DATE)"],
        "next_quarter":   ["CURRENT_DATE", "DATEADD(quarter,1,CURRENT_DATE)"],
        "next_year":      ["CURRENT_DATE", "DATEADD(year,1,CURRENT_DATE)"],
    }

    all_presets = {**_PAST, **_FORWARD}
    if v in all_presets:
        start, end = all_presets[v]
        return {"operator": "BETWEEN_SQL", "value": [start, end], "is_raw_sql": True}

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

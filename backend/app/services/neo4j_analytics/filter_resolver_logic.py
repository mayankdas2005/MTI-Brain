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
            if alias.lower() == user_lower:
                return canonical, 100.0, []

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
    """Tier 3: map standardized temporal keywords to Redshift SQL expressions.

    The LLM outputs keywords (e.g. 'last_30_days', 'this_month', 'q4_2024').
    We translate them to Redshift-native SQL so CURRENT_DATE is evaluated at
    query time — no hardcoded dates, no need for the LLM to know today's date.

    Returns one of:
      {"operator": ">=",          "value": "<sql_expr>",             "is_raw_sql": True}
      {"operator": "BETWEEN_SQL", "value": ["<sql_expr>","<sql_expr>"], "is_raw_sql": True}
      {"operator": "BETWEEN",     "value": ["YYYY-MM-DD","YYYY-MM-DD"]} (specific quarter)
    or None if the value is not a recognized temporal keyword.
    """
    import re
    v = user_value.strip().lower().replace(" ", "_")

    # last_N_days / last_N_day
    m = re.match(r"last[_]?(\d+)[_]?days?$", v)
    if m:
        n = m.group(1)
        return {"operator": ">=", "value": f"DATEADD(day, -{n}, CURRENT_DATE)", "is_raw_sql": True}

    # last_N_months
    m = re.match(r"last[_]?(\d+)[_]?months?$", v)
    if m:
        n = m.group(1)
        return {"operator": ">=", "value": f"DATEADD(month, -{n}, CURRENT_DATE)", "is_raw_sql": True}

    # last_N_years
    m = re.match(r"last[_]?(\d+)[_]?years?$", v)
    if m:
        n = m.group(1)
        return {"operator": ">=", "value": f"DATEADD(year, -{n}, CURRENT_DATE)", "is_raw_sql": True}

    # today
    if v == "today":
        return {"operator": "=", "value": "CURRENT_DATE", "is_raw_sql": True}

    # yesterday
    if v == "yesterday":
        return {"operator": "=", "value": "DATEADD(day, -1, CURRENT_DATE)", "is_raw_sql": True}

    # this_month / mtd / month_to_date
    if v in ("this_month", "mtd", "month_to_date", "current_month"):
        return {"operator": ">=", "value": "DATE_TRUNC('month', CURRENT_DATE)", "is_raw_sql": True}

    # last_month
    if v in ("last_month", "prior_month", "previous_month"):
        return {
            "operator": "BETWEEN_SQL",
            "value": [
                "DATE_TRUNC('month', DATEADD(month, -1, CURRENT_DATE))",
                "LAST_DAY(DATEADD(month, -1, CURRENT_DATE))",
            ],
            "is_raw_sql": True,
        }

    # this_quarter / qtd
    if v in ("this_quarter", "qtd", "quarter_to_date", "current_quarter"):
        return {"operator": ">=", "value": "DATE_TRUNC('quarter', CURRENT_DATE)", "is_raw_sql": True}

    # last_quarter / prior_quarter
    if v in ("last_quarter", "prior_quarter", "previous_quarter"):
        return {
            "operator": "BETWEEN_SQL",
            "value": [
                "DATE_TRUNC('quarter', DATEADD(quarter, -1, CURRENT_DATE))",
                "DATEADD(day, -1, DATE_TRUNC('quarter', CURRENT_DATE))",
            ],
            "is_raw_sql": True,
        }

    # ytd / this_year / year_to_date
    if v in ("ytd", "this_year", "year_to_date", "current_year"):
        return {"operator": ">=", "value": "DATE_TRUNC('year', CURRENT_DATE)", "is_raw_sql": True}

    # last_year / prior_year
    if v in ("last_year", "prior_year", "previous_year"):
        return {
            "operator": "BETWEEN_SQL",
            "value": [
                "DATE_TRUNC('year', DATEADD(year, -1, CURRENT_DATE))",
                "DATEADD(day, -1, DATE_TRUNC('year', CURRENT_DATE))",
            ],
            "is_raw_sql": True,
        }

    # Specific quarter: q4_2024 / q4 2024 / Q4_2024
    m = re.match(r"q([1-4])[_\s]?(\d{4})$", v)
    if m:
        q, year = int(m.group(1)), int(m.group(2))
        q_map = {1: ("01-01", "03-31"), 2: ("04-01", "06-30"), 3: ("07-01", "09-30"), 4: ("10-01", "12-31")}
        start, end = q_map[q]
        return {"operator": "BETWEEN", "value": [f"{year}-{start}", f"{year}-{end}"]}

    # Single ISO date YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", user_value.strip()):
        return {"operator": "=", "value": user_value.strip()}

    # ISO range: YYYY-MM-DD TO YYYY-MM-DD (LLM may still produce this)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$", v)
    if m:
        return {"operator": "BETWEEN", "value": [m.group(1), m.group(2)]}

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

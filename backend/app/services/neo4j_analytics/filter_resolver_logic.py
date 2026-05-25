"""Filter value resolution logic — pure functions, no I/O.

The tiered resolution pipeline:
  Tier 1: Exact match against Column.value_aliases and sample_values
  Tier 2: Fuzzy match against Column.sample_values via rapidfuzz
  Tier 3: Temporal expression parsing
  Tier 4: Redshift DISTINCT probe (I/O — not in this file, called by filter_resolver node)
  Tier 5: LLM disambiguation (not in this file)
  Tier 6: Clarification request
"""

from __future__ import annotations

from datetime import date, timedelta


def resolve_tier1_exact(
    user_value: str,
    sample_values: list[str],
    value_aliases: dict[str, str] | None,
) -> str | None:
    """Tier 1: exact match against aliases and sample_values."""
    if value_aliases:
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
    """Tier 2: fuzzy match against sample_values.

    Returns (resolved_value, score, candidates_above_85).
    resolved_value is None if ambiguous or below threshold.
    """
    if not sample_values:
        return None, 0.0, []

    # Skip fuzzy matching for very large vocabularies — go to Tier 4
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
    """Tier 3: parse temporal expressions into ISO date ranges.

    Returns {"operator": "BETWEEN", "value": ["YYYY-MM-DD", "YYYY-MM-DD"]}
    or None if not a temporal expression.
    """
    value_lower = user_value.lower().strip()
    today = date.today()

    if value_lower in ("today",):
        return {"operator": "=", "value": str(today)}

    if value_lower in ("yesterday",):
        return {"operator": "=", "value": str(today - timedelta(days=1))}

    if value_lower in ("this month", "mtd", "month to date"):
        start = today.replace(day=1)
        return {"operator": "BETWEEN", "value": [str(start), str(today)]}

    if value_lower in ("last month",):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return {"operator": "BETWEEN", "value": [str(first_prev), str(last_prev)]}

    if value_lower in ("ytd", "year to date", "this year"):
        start = today.replace(month=1, day=1)
        return {"operator": "BETWEEN", "value": [str(start), str(today)]}

    if value_lower in ("last year",):
        start = date(today.year - 1, 1, 1)
        end = date(today.year - 1, 12, 31)
        return {"operator": "BETWEEN", "value": [str(start), str(end)]}

    # Last N days
    import re
    m = re.match(r"last\s+(\d+)\s+days?", value_lower)
    if m:
        n = int(m.group(1))
        start = today - timedelta(days=n)
        return {"operator": "BETWEEN", "value": [str(start), str(today)]}

    # Last N months
    m = re.match(r"last\s+(\d+)\s+months?", value_lower)
    if m:
        n = int(m.group(1))
        start = _subtract_months(today, n)
        return {"operator": "BETWEEN", "value": [str(start), str(today)]}

    # This quarter
    if value_lower in ("this quarter", "current quarter"):
        q_start = _quarter_start(today)
        return {"operator": "BETWEEN", "value": [str(q_start), str(today)]}

    # Last quarter
    if value_lower in ("last quarter", "prior quarter"):
        q_start = _quarter_start(today)
        prev_q_end = q_start - timedelta(days=1)
        prev_q_start = _quarter_start(prev_q_end)
        return {"operator": "BETWEEN", "value": [str(prev_q_start), str(prev_q_end)]}

    # Q1/Q2/Q3/Q4 YYYY patterns
    m = re.match(r"q([1-4])\s+(\d{4})", value_lower)
    if m:
        q, year = int(m.group(1)), int(m.group(2))
        q_map = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
        start_m, end_m = q_map[q]
        start = date(year, start_m, 1)
        end_day = _last_day_of_month(year, end_m)
        end = date(year, end_m, end_day)
        return {"operator": "BETWEEN", "value": [str(start), str(end)]}

    # Try dateparser as fallback
    try:
        import dateparser
        parsed = dateparser.parse(user_value)
        if parsed:
            return {"operator": "=", "value": str(parsed.date())}
    except ImportError:
        pass

    return None


def _quarter_start(d: date) -> date:
    quarter = (d.month - 1) // 3
    return date(d.year, quarter * 3 + 1, 1)


def _last_day_of_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


def _subtract_months(d: date, n: int) -> date:
    month = d.month - n
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(d.day, _last_day_of_month(year, month))
    return date(year, month, day)


def build_redshift_probe_sql(table_fqn: str, col_name: str, user_value: str) -> str:
    """Build a parameterized DISTINCT probe SQL (returns template with placeholder)."""
    return (
        f"SELECT DISTINCT CAST({col_name} AS VARCHAR) AS val "
        f"FROM {table_fqn} "
        f"WHERE LOWER(CAST({col_name} AS VARCHAR)) LIKE %s "
        f"LIMIT 50"
    )


def build_redshift_probe_params(user_value: str) -> list:
    """Returns params list for the probe SQL."""
    return [f"%{user_value.lower()}%"]


def is_time_sensitive_sql(sql: str) -> bool:
    """Returns True if the SQL contains time-sensitive functions — skip caching."""
    sql_upper = sql.upper()
    return any(fn in sql_upper for fn in ["GETDATE()", "SYSDATE", "CURRENT_DATE", "CURRENT_TIMESTAMP"])

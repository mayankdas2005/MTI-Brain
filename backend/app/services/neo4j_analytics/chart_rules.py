"""Deterministic chart type selection rules.

Pure function — no I/O, no LLM. Selects chart type from result shape,
column types, row count, semantic intent, and persona.

Persona tiers (from most to least detail-tolerant):
  analyst   — max information density; all chart types valid
  manager   — visual summaries; no scatter/bubble
  director  — portfolio-level; prefer area/KPI; no scatter/bubble/heatmap
  executive — absolute simplicity; KPI cards, single-series, pie/donut only
"""

from __future__ import annotations

# ── Table row-count thresholds per persona ────────────────────────────────────
# Above these limits the result flips to a table regardless of shape.
# Time series is exempt — 365 daily rows should still be a line chart.

_TABLE_THRESHOLD: dict[str, int] = {
    "analyst":   100,
    "manager":    50,
    "director":   30,
    "executive":  10,
}

_DEFAULT_TABLE_THRESHOLD = 50


def select_chart_type(
    columns: list[str],
    rows: list[list],
    result_shape: str,
    intent: str = "",
    persona: str = "executive",
) -> str:
    """Select the most appropriate chart type.

    Returns one of: kpi_card, line, multi_line, area, stacked_area, bar,
    bar_horizontal, grouped_bar, stacked_bar, pie, donut, scatter, bubble,
    heatmap, waterfall, dual_axis, table.
    """
    if not rows or not columns:
        return "table"

    n_rows = len(rows)
    n_cols = len(columns)
    col_types = _classify_column_types(columns, rows)
    n_date = col_types.count("date")
    n_numeric = col_types.count("numeric")
    n_string = col_types.count("string")
    intent_lower = intent.lower()
    table_limit = _TABLE_THRESHOLD.get(persona, _DEFAULT_TABLE_THRESHOLD)

    # ── KPI card ──────────────────────────────────────────────────────────────
    # Executives get a more aggressive KPI threshold (≤ 5 rows of pure numbers)
    kpi_row_limit = 5 if persona == "executive" else 3
    if result_shape == "kpi" or (n_rows <= kpi_row_limit and n_numeric == n_cols):
        return "kpi_card"

    # ── Force table for very wide or text-heavy results ───────────────────────
    # if n_cols > 5 or _has_long_text(columns, rows):
    #     return "table"

    # ── Waterfall — variance / attribution intent ─────────────────────────────
    # Skip for executives (too complex) and directors (borderline — allow).
    _WATERFALL_KEYWORDS = ("variance", "waterfall", "attribution", "delta",
                           "change vs", "vs prior", "vs last", "period over period",
                           "mom", "qoq", "yoy contribution")
    if (
        persona not in ("executive",)
        and n_string == 1
        and n_numeric >= 1
        and any(kw in intent_lower for kw in _WATERFALL_KEYWORDS)
        and _has_mixed_sign(col_types, rows)
    ):
        return "waterfall"

    # ── Time series ───────────────────────────────────────────────────────────
    if result_shape == "time_series" or n_date >= 1:
        return _time_series_chart(n_date, n_string, n_numeric, intent_lower, persona)

    # ── Categorical × measure ─────────────────────────────────────────────────
    if n_string == 1 and n_numeric == 1:
        if n_rows > table_limit:
            return "table"
        label_max = _max_label_length(col_types, rows)
        if label_max > 20:
            return "bar_horizontal"
        # Executives and directors prefer donut for small sets (feel of proportion)
        if n_rows <= 5:
            return "donut" if persona in ("executive", "director") else "pie"
        if n_rows <= 7 and persona == "analyst":
            return "pie"
        return "bar"

    # ── Two categoricals × one measure ───────────────────────────────────────
    if n_string == 2 and n_numeric == 1:
        # Executives: flatten to bar (simplify away the grouping)
        if persona == "executive":
            return "bar"
        if "breakdown" in intent_lower or "composition" in intent_lower or "part" in intent_lower:
            return "stacked_bar"
        if "compare" in intent_lower or "comparison" in intent_lower:
            return "grouped_bar"
        return "grouped_bar"

    # ── Scatter / bubble — analysts only ─────────────────────────────────────
    if persona == "analyst":
        if n_numeric == 2 and n_string == 0 and n_date == 0:
            return "scatter"
        if n_numeric == 3 and n_string == 0:
            return "bubble"

    # ── Heatmap — analysts and managers only ─────────────────────────────────
    if persona in ("analyst", "manager") and n_string == 2 and n_numeric == 1:
        return "heatmap"

    # ── Dual-axis — analysts and managers only ────────────────────────────────
    if persona in ("analyst", "manager") and n_numeric == 2 and n_string == 1:
        if "vs" in intent_lower or "against" in intent_lower or "dual" in intent_lower:
            return "dual_axis"

    # ── Row-count fallback to table ───────────────────────────────────────────
    if n_rows > table_limit and result_shape != "time_series":
        return "table"

    if n_rows > 20:
        return "bar"

    return "table"


# ── Time series selection ─────────────────────────────────────────────────────

def _time_series_chart(
    n_date: int, n_string: int, n_numeric: int, intent_lower: str, persona: str
) -> str:
    if n_date == 1 and n_string == 0 and n_numeric == 1:
        # Executives/directors: area for cumulative/volume feel
        # Analysts/managers: line for precision
        return "area" if persona in ("executive", "director") else "line"

    if n_date == 1 and n_string == 1 and n_numeric == 1:
        # Executives see a single aggregate area/line — strip the category breakdown
        if persona == "executive":
            return "area"
        return _time_series_with_category(intent_lower, persona)

    if n_date == 1 and n_numeric >= 2:
        # Executives: collapse to area (one series)
        if persona == "executive":
            return "area"
        return "multi_line"

    return "area" if persona in ("executive", "director") else "line"


def _time_series_with_category(intent_lower: str, persona: str) -> str:
    if "breakdown" in intent_lower or "composition" in intent_lower:
        return "stacked_area"
    if "trend" in intent_lower or "over time" in intent_lower or "history" in intent_lower:
        return "multi_line"
    if "compare" in intent_lower:
        return "multi_line"
    # Directors prefer stacked_area (portfolio view), analysts prefer multi_line (per-series)
    return "stacked_area" if persona == "director" else "multi_line"


# ── Column classification helpers ─────────────────────────────────────────────

def _classify_column_types(columns: list[str], rows: list[list]) -> list[str]:
    import re
    result = []
    for i in range(len(columns)):
        sample = [r[i] for r in rows[:20] if r[i] is not None]
        if not sample:
            result.append("unknown")
            continue
        try:
            [float(v) for v in sample]
            result.append("numeric")
            continue
        except (TypeError, ValueError):
            pass
        if re.match(r"\d{4}-\d{2}-\d{2}", str(sample[0])):
            result.append("date")
            continue
        result.append("string")
    return result


def _has_long_text(columns: list[str], rows: list[list]) -> bool:
    for i in range(len(columns)):
        sample = [str(r[i]) for r in rows[:10] if r[i] is not None]
        if sample and max(len(s) for s in sample) > 100:
            return True
    return False


def _max_label_length(col_types: list[str], rows: list[list]) -> int:
    max_len = 0
    for i, t in enumerate(col_types):
        if t == "string":
            sample = [str(r[i]) for r in rows[:20] if r[i] is not None]
            if sample:
                max_len = max(max_len, max(len(s) for s in sample))
    return max_len


def _has_mixed_sign(col_types: list[str], rows: list[list]) -> bool:
    for i, t in enumerate(col_types):
        if t == "numeric":
            vals = [float(r[i]) for r in rows if r[i] is not None]
            if vals and any(v > 0 for v in vals) and any(v < 0 for v in vals):
                return True
    return False

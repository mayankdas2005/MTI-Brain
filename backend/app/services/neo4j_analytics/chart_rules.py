"""Deterministic chart type selection rules.

Pure function — no I/O, no LLM. Selects chart type from result shape,
column types, row count, and semantic intent.
"""

from __future__ import annotations


def select_chart_type(
    columns: list[str],
    rows: list[list],
    result_shape: str,
    intent: str = "",
) -> str:
    """Select the most appropriate chart type for the given result set.

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

    if result_shape == "kpi" or (n_rows <= 3 and n_numeric == n_cols):
        return "kpi_card"

    if n_cols > 5 or _has_long_text(columns, rows):
        return "table"

    if result_shape == "time_series" or n_date >= 1:
        if n_date == 1 and n_string == 0 and n_numeric == 1:
            return "line"
        if n_date == 1 and n_string == 1 and n_numeric == 1:
            return _time_series_with_category(intent_lower)
        if n_date == 1 and n_numeric >= 2:
            return "multi_line"
        return "line"

    if n_string == 1 and n_numeric == 1:
        if n_rows > 50:
            return "table"
        label_max = _max_label_length(columns, col_types, rows)
        if label_max > 20:
            return "bar_horizontal"
        if n_rows <= 5:
            return "pie"
        return "bar"

    if n_string == 2 and n_numeric == 1:
        if "breakdown" in intent_lower or "composition" in intent_lower or "part" in intent_lower:
            return "stacked_bar"
        if "compare" in intent_lower or "comparison" in intent_lower:
            return "grouped_bar"
        return "grouped_bar"

    if n_numeric == 2 and n_string == 0 and n_date == 0:
        return "scatter"

    if n_numeric == 3 and n_string == 0:
        return "bubble"

    if n_string == 2 and n_numeric == 1:
        return "heatmap"

    if n_rows > 50 and result_shape != "time_series":
        return "table"

    if n_rows > 20:
        return "bar"

    return "table"


def _time_series_with_category(intent_lower: str) -> str:
    if "trend" in intent_lower or "over time" in intent_lower or "history" in intent_lower:
        return "multi_line"
    if "breakdown" in intent_lower or "composition" in intent_lower:
        return "stacked_area"
    if "compare" in intent_lower:
        return "multi_line"
    return "multi_line"


def _classify_column_types(columns: list[str], rows: list[list]) -> list[str]:
    import re
    result = []
    for i, col in enumerate(columns):
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
    for i, col in enumerate(columns):
        sample = [str(r[i]) for r in rows[:10] if r[i] is not None]
        if sample and max(len(s) for s in sample) > 100:
            return True
    return False


def _max_label_length(columns: list[str], col_types: list[str], rows: list[list]) -> int:
    max_len = 0
    for i, t in enumerate(col_types):
        if t == "string":
            sample = [str(r[i]) for r in rows[:20] if r[i] is not None]
            if sample:
                max_len = max(max_len, max(len(s) for s in sample))
    return max_len

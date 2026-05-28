"""Result summarizer: raw query rows → QuerySummary.

Pure function — no I/O. Produces the summary passed to synthesis LLM.
Raw rows are never passed to any LLM directly.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from app.services.agents.semantic_ir import ColumnStat, QuerySummary


def summarize_results(
    columns: list[str],
    rows: list[list],
    intent: str = "",
    reliability_flags: list[str] | None = None,
    result_label: str | None = None,
) -> QuerySummary:
    """Build a QuerySummary from raw query results."""
    if not columns:
        return QuerySummary(
            total_rows=0,
            columns=[],
            sample_rows=[],
            result_shape="flat",
            reliability_flags=reliability_flags or [],
            result_label=result_label,
        )

    total_rows = len(rows)
    col_stats = [_compute_column_stat(col, i, rows) for i, col in enumerate(columns)]
    result_shape = _detect_result_shape(columns, rows, intent)
    sample_rows = _sample_rows(columns, rows, result_shape)

    return QuerySummary(
        total_rows=total_rows,
        columns=col_stats,
        sample_rows=sample_rows,
        result_shape=result_shape,
        reliability_flags=reliability_flags or [],
        result_label=result_label,
    )


def _compute_column_stat(col_name: str, col_idx: int, rows: list[list]) -> ColumnStat:
    all_vals = [r[col_idx] for r in rows]
    null_count = sum(1 for v in all_vals if v is None)
    vals = [v for v in all_vals if v is not None]
    total_count = len(all_vals)
    distinct_count = len(set(str(v) for v in vals)) if vals else 0

    if not vals:
        return ColumnStat(
            name=col_name, dtype="unknown", null_count=null_count,
            total_count=total_count, distinct_count=0,
            min=None, max=None, mean=None, median=None, mode=None, top_values=None,
        )

    dtype = _infer_dtype(vals)

    if dtype == "numeric":
        nums = [float(v) for v in vals]
        nums_sorted = sorted(nums)
        return ColumnStat(
            name=col_name, dtype="numeric", null_count=null_count,
            total_count=total_count, distinct_count=distinct_count,
            min=min(nums), max=max(nums),
            mean=sum(nums) / len(nums),
            median=_percentile(nums_sorted, 0.5),
            mode=None, top_values=None,
        )

    if dtype == "date":
        str_vals = [str(v) for v in vals]
        sorted_dates = sorted(str_vals)
        return ColumnStat(
            name=col_name, dtype="date", null_count=null_count,
            total_count=total_count, distinct_count=distinct_count,
            min=sorted_dates[0], max=sorted_dates[-1],
            mean=None, median=None, mode=None, top_values=None,
        )

    str_vals = [str(v) for v in vals]
    top = Counter(str_vals).most_common(5)
    return ColumnStat(
        name=col_name, dtype="string", null_count=null_count,
        total_count=total_count, distinct_count=distinct_count,
        min=None, max=None, mean=None, median=None, mode=None,
        top_values=top,
    )


def _infer_dtype(vals: list) -> str:
    if not vals:
        return "unknown"
    sample = vals[:20]
    try:
        [float(v) for v in sample]
        return "numeric"
    except (TypeError, ValueError):
        pass
    str_sample = [str(v) for v in sample]
    if str_sample and re.match(r"\d{4}-\d{2}-\d{2}", str_sample[0]):
        return "date"
    return "string"


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _detect_result_shape(columns: list[str], rows: list[list], intent: str) -> str:
    """Determine the result shape for chart type selection."""
    if not rows:
        return "flat"

    if len(rows) <= 3 and all(_infer_dtype([r[i] for r in rows if r[i] is not None]) == "numeric" for i in range(min(len(columns), 3))):
        return "kpi"

    col_types = [_infer_dtype([r[i] for r in rows if r[i] is not None]) for i in range(len(columns))]
    has_date = "date" in col_types
    has_numeric = "numeric" in col_types

    if has_date and has_numeric:
        return "time_series"

    intent_lower = intent.lower()
    if any(kw in intent_lower for kw in ["rank", "top", "bottom", "highest", "lowest"]):
        return "ranking"

    if sum(1 for t in col_types if t == "string") >= 2 and has_numeric:
        return "cross_tab"

    return "flat"


def _sample_rows(columns: list[str], rows: list[list], result_shape: str) -> list[dict]:
    """Return ≤20 representative rows as dicts."""
    if not rows:
        return []

    if result_shape == "kpi" or len(rows) <= 20:
        sampled = rows[:20]
    elif result_shape == "time_series":
        sampled = rows[:10] + rows[-10:]
    else:
        n = len(rows)
        head = rows[:4]
        mid_start = max(4, n // 2 - 2)
        middle = rows[mid_start:mid_start + 4]
        tail = rows[max(0, n - 4):]
        seen_ids = set()
        sampled = []
        for r in head + middle + tail:
            rid = id(r)
            if rid not in seen_ids:
                seen_ids.add(rid)
                sampled.append(r)
        sampled = sampled[:20]

    return [dict(zip(columns, row)) for row in sampled]

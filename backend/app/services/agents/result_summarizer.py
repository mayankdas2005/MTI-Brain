"""Result summarizer: raw query rows → QuerySummary.

Pure function — no I/O. Produces the summary passed to synthesis LLM.
Raw rows are never passed to any LLM directly.
"""

from __future__ import annotations

import pandas as pd

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

    df = pd.DataFrame(rows, columns=columns)
    col_stats = [_compute_column_stat_pd(col, df[col]) for col in columns]
    result_shape = _detect_result_shape_pd(df, intent)
    sample_rows = _sample_rows(columns, rows, result_shape)

    return QuerySummary(
        total_rows=len(df),
        columns=col_stats,
        sample_rows=sample_rows,
        result_shape=result_shape,
        reliability_flags=reliability_flags or [],
        result_label=result_label,
    )


def _compute_column_stat_pd(col_name: str, series: pd.Series) -> ColumnStat:
    null_count = int(series.isna().sum())
    total_count = len(series)
    non_null = series.dropna()
    distinct_count = int(non_null.nunique())

    if non_null.empty:
        return ColumnStat(
            name=col_name, dtype="unknown", null_count=null_count,
            total_count=total_count, distinct_count=0,
        )

    if pd.api.types.is_numeric_dtype(series):
        desc = non_null.astype(float).describe()
        return ColumnStat(
            name=col_name, dtype="numeric",
            null_count=null_count, total_count=total_count, distinct_count=distinct_count,
            min=float(desc["min"]), max=float(desc["max"]),
            mean=float(desc["mean"]), median=float(desc["50%"]),
            std=float(desc["std"]) if "std" in desc else None,
            p25=float(desc["25%"]), p75=float(desc["75%"]),
        )

    str_series = non_null.astype(str)
    if str_series.str.match(r"\d{4}-\d{2}-\d{2}").any():
        sorted_vals = sorted(str_series.tolist())
        return ColumnStat(
            name=col_name, dtype="date",
            null_count=null_count, total_count=total_count, distinct_count=distinct_count,
            min=sorted_vals[0], max=sorted_vals[-1],
        )

    top = list(str_series.value_counts().head(5).items())
    return ColumnStat(
        name=col_name, dtype="string",
        null_count=null_count, total_count=total_count, distinct_count=distinct_count,
        top_values=top,
    )


def _detect_result_shape_pd(df: pd.DataFrame, intent: str) -> str:
    if df.empty:
        return "flat"

    has_numeric = any(pd.api.types.is_numeric_dtype(df[c]) for c in df.columns)
    has_date = any(
        df[c].dropna().astype(str).str.match(r"\d{4}-\d{2}-\d{2}").any()
        for c in df.columns if df[c].dtype == object and not df[c].dropna().empty
    )
    n_string = sum(
        1 for c in df.columns
        if df[c].dtype == object
        and not (df[c].dropna().astype(str).str.match(r"\d{4}-\d{2}-\d{2}").any()
                 if not df[c].dropna().empty else False)
    )

    if len(df) <= 3 and has_numeric and not has_date:
        return "kpi"
    if has_date and has_numeric:
        return "time_series"

    intent_lower = intent.lower()
    if any(kw in intent_lower for kw in ["rank", "top", "bottom", "highest", "lowest"]):
        return "ranking"
    if n_string >= 2 and has_numeric:
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
        seen_ids: set = set()
        sampled = []
        for r in head + middle + tail:
            rid = id(r)
            if rid not in seen_ids:
                seen_ids.add(rid)
                sampled.append(r)
        sampled = sampled[:20]

    return [dict(zip(columns, row)) for row in sampled]

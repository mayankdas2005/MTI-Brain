"""Result summarizer: raw query rows → QuerySummary.

Pure function — no I/O. Produces the summary passed to synthesis LLM.
Raw rows are never passed to any LLM directly.
"""

from __future__ import annotations

import pandas as pd
from datetime import date, datetime
from decimal import Decimal

from app.services.agents.semantic_ir import ColumnStat, QuerySummary

_SAMPLE_BUDGET = 50


def summarize_results(
    columns: list[str],
    rows: list[list],
    intent: str = "",
    reliability_flags: list[str] | None = None,
    result_label: str | None = None,
    was_truncated: bool = False,
    true_stats: dict | None = None,
    stats_source: str = "capped",
) -> QuerySummary:
    """Build a QuerySummary from raw query results.

    When was_truncated=True and true_stats is provided, key ColumnStat fields
    (distinct_count, min, max, mean, std, null_count, total_count) are patched
    from the full-result SQL aggregate, making them accurate for the complete
    dataset rather than the capped sample.
    """
    flags = list(reliability_flags or [])

    if not columns:
        return QuerySummary(
            total_rows=0,
            columns=[],
            sample_rows=[],
            result_shape="flat",
            reliability_flags=flags,
            result_label=result_label,
            was_truncated=was_truncated,
            true_total_rows=true_stats.get("total_rows") if true_stats else None,
            stats_source=stats_source,
        )

    df = pd.DataFrame(rows, columns=columns)
    col_stats = [_compute_column_stat_pd(col, df[col]) for col in columns]
    result_shape = _detect_result_shape_pd(df, intent)

    true_total = None
    if true_stats:
        true_total = true_stats.get("total_rows")
        total_rows_for_stat = int(true_total) if true_total else len(df)
        for cs in col_stats:
            safe = cs.name.replace('"', "")
            null_c = true_stats.get(f"{safe}__null_count")
            if null_c is not None:
                cs.null_count = int(null_c)
                cs.total_count = total_rows_for_stat
            mn = true_stats.get(f"{safe}__min")
            if mn is not None:
                cs.min = str(mn) if isinstance(mn, (date, datetime)) else float(mn) if isinstance(mn, Decimal) else mn
            mx = true_stats.get(f"{safe}__max")
            if mx is not None:
                cs.max = str(mx) if isinstance(mx, (date, datetime)) else float(mx) if isinstance(mx, Decimal) else mx
            mean = true_stats.get(f"{safe}__mean")
            if mean is not None:
                cs.mean = float(mean)
            std = true_stats.get(f"{safe}__std")
            if std is not None:
                cs.std = float(std)
            distinct = true_stats.get(f"{safe}__distinct")
            if distinct is not None:
                cs.distinct_count = int(distinct)

    if was_truncated:
        cap = len(rows)
        src_label = (
            "full Redshift result (exact)"
            if stats_source == "full_result"
            else f"first {cap} rows (approximate)"
        )
        count_note = f" ({true_total:,} total)" if true_total else ""
        flags.append(
            f"RESULT TRUNCATED: query returned more than {cap} rows{count_note}. "
            f"Showing {cap}-row sample for display. "
            f"Stats (distinct counts, min, max, mean, std) from {src_label}."
        )

    sample_rows = _smart_sample(columns, rows)

    return QuerySummary(
        total_rows=len(df),
        columns=col_stats,
        sample_rows=sample_rows,
        result_shape=result_shape,
        reliability_flags=flags,
        result_label=result_label,
        was_truncated=was_truncated,
        true_total_rows=true_total,
        stats_source=stats_source,
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


def _smart_sample(columns: list[str], rows: list[list], budget: int = _SAMPLE_BUDGET) -> list[dict]:
    """Stratified 4-tier sample from capped rows.

    Tier 1 — temporal boundaries (≤2 rows): min/max date rows.
    Tier 2 — extreme value rows (≤10 rows): max abs value per numeric col.
    Tier 3 — dimension coverage (≤15 rows): one row per unique value for
              low-cardinality string cols (distinct ≤ 30).
    Tier 4 — stratified fill (remaining budget): spread across unselected rows.

    Each row dict gets a "_sample_tier" key for LLM context.
    """
    if not rows:
        return []
    if len(rows) <= budget:
        return [dict(zip(columns, row)) | {"_sample_tier": "all"} for row in rows]

    df = pd.DataFrame(rows, columns=columns)

    date_cols = [
        c for c in columns
        if df[c].dtype == object
        and not df[c].dropna().empty
        and df[c].dropna().astype(str).str.match(r"\d{4}-\d{2}-\d{2}").any()
    ]
    numeric_cols = [c for c in columns if pd.api.types.is_numeric_dtype(df[c])]
    string_cols = [c for c in columns if c not in date_cols and c not in numeric_cols]

    selected: dict[int, str] = {}

    # Tier 1 — temporal boundaries
    if date_cols:
        dc = date_cols[0]
        str_vals = df[dc].dropna().astype(str)
        if not str_vals.empty:
            selected[int(str_vals.idxmin())] = "boundary"
            selected[int(str_vals.idxmax())] = "boundary"

    # Tier 2 — extreme value rows (up to 10)
    outlier_budget = int(budget * 0.20)
    for nc in numeric_cols:
        if len(selected) - 2 >= outlier_budget:
            break
        col_abs = pd.to_numeric(df[nc], errors="coerce").abs()
        if col_abs.dropna().empty:
            continue
        idx = int(col_abs.idxmax())
        if idx not in selected:
            selected[idx] = "outlier"

    # Tier 3 — dimension coverage (up to 15)
    coverage_budget = int(budget * 0.30)
    coverage_count = 0
    for sc in string_cols:
        if coverage_count >= coverage_budget:
            break
        uniq = df[sc].dropna().unique()
        if len(uniq) > 30:
            continue
        for val in uniq:
            if coverage_count >= coverage_budget:
                break
            matches = df.index[df[sc] == val].tolist()
            if matches:
                idx = int(matches[0])
                if idx not in selected:
                    selected[idx] = "coverage"
                    coverage_count += 1

    # Tier 4 — stratified fill
    remaining = budget - len(selected)
    if remaining > 0:
        unselected = [i for i in range(len(rows)) if i not in selected]
        if unselected and numeric_cols:
            nc = numeric_cols[0]
            vals = pd.to_numeric(df[nc], errors="coerce")
            u_vals = vals.iloc[unselected]
            for p in (0.1, 0.25, 0.5, 0.75, 0.9):
                if len(selected) >= budget:
                    break
                target = u_vals.quantile(p)
                closest = int((u_vals - target).abs().idxmin())
                if closest not in selected:
                    selected[closest] = "representative"
        if len(selected) < budget:
            unselected = [i for i in range(len(rows)) if i not in selected]
            step = max(1, len(unselected) // max(1, budget - len(selected)))
            for i in range(0, len(unselected), step):
                if len(selected) >= budget:
                    break
                selected[int(unselected[i])] = "representative"

    result = []
    for idx, tier in selected.items():
        if idx < len(rows):
            row_dict = dict(zip(columns, rows[idx]))
            row_dict["_sample_tier"] = tier
            result.append(row_dict)

    return result[:budget]

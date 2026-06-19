"""Deep analysis node: threshold sensitivity analysis.

Runs only when deep_analysis=True and the query has numeric threshold filters.
Re-executes lightweight COUNT+SUM variants at 0.5x and 2x the original threshold
so users can see how sensitive their result is to the threshold they chose.
"""

from __future__ import annotations
import re

from app.core.logger import logger
from app.services.agents.state import AnalyticsState


def _extract_numeric_thresholds(state: AnalyticsState) -> list[dict]:
    """Return numeric threshold candidates from semantic_ir_list[0]."""
    ir_list = state.get("semantic_ir_list") or []
    if not ir_list:
        return []
    ir = ir_list[0]

    candidates: list[dict] = []

    # threshold_specs (HAVING-style computed thresholds)
    for ts in (ir.get("threshold_specs") or []):
        val = ts.get("value")
        op = ts.get("operator")
        if val is not None and op in (">", "<", ">=", "<="):
            try:
                candidates.append({
                    "value": float(val),
                    "operator": op,
                    "label": ts.get("label") or ts.get("expression") or "threshold",
                    "source": "threshold_spec",
                })
            except (TypeError, ValueError):
                pass

    # numeric range filters from filters list (WHERE-style)
    for f in (ir.get("filters") or []):
        op = f.get("operator") or ""
        if op not in (">", "<", ">=", "<="):
            continue
        raw_val = f.get("value")
        if isinstance(raw_val, list):
            continue
        try:
            numeric_val = float(str(raw_val).replace(",", "").replace("$", ""))
            if numeric_val > 0:
                candidates.append({
                    "value": numeric_val,
                    "operator": op,
                    "label": (f.get("raw_user_value") or f.get("column_name") or "filter"),
                    "column": f.get("column_name"),
                    "table": f.get("table_fqn"),
                    "source": "filter",
                })
        except (TypeError, ValueError):
            pass

    # De-duplicate by (value, operator)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for c in candidates:
        key = (c["value"], c["operator"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:2]  # cap at 2 thresholds to limit extra SQL calls


def _substitute_threshold(sql: str, original_value: float, new_value: float) -> str:
    """Replace the first occurrence of the original threshold value in the SQL."""
    # Format original and new values to match SQL representation
    # Try integer first, then float
    orig_int = int(original_value) if original_value == int(original_value) else None
    orig_patterns = [str(original_value), str(orig_int) if orig_int is not None else None]
    orig_patterns = [p for p in orig_patterns if p]

    new_repr = str(int(new_value)) if new_value == int(new_value) else str(new_value)

    for pattern in orig_patterns:
        # Only replace when the value is a standalone number (not part of a larger number)
        escaped = re.escape(pattern)
        replaced = re.sub(
            r"(?<![0-9.])" + escaped + r"(?![0-9.])",
            new_repr,
            sql,
            count=1,
        )
        if replaced != sql:
            return replaced

    return sql  # if substitution fails, return original unchanged


def _build_probe_sql(original_sql: str, threshold: dict, new_value: float) -> str | None:
    """Build a lightweight COUNT(*) + SUM probe from the original SQL with a varied threshold."""
    modified_sql = _substitute_threshold(original_sql, threshold["value"], new_value)
    if modified_sql == original_sql and threshold["value"] != new_value:
        return None  # substitution failed — skip

    # Wrap in a COUNT/SUM aggregate to keep it lightweight
    # Only valid when original SQL is a simple SELECT (not a CTE-heavy query using WITH)
    stripped = modified_sql.strip().rstrip(";")

    # Wrap as subquery to get count and sum of the first numeric measure column
    probe = f"SELECT COUNT(*) AS _probe_count FROM ({stripped}) AS _probe_subq"
    return probe


async def deep_sensitivity(state: AnalyticsState) -> dict:
    if not state.get("deep_analysis"):
        return {"sensitivity_table": None}

    sql_list = state.get("sql_list") or []
    if not sql_list or not sql_list[0]:
        return {"sensitivity_table": None}

    thresholds = _extract_numeric_thresholds(state)
    if not thresholds:
        logger.info("deep_sensitivity | no numeric thresholds found | thread={}", state.get("thread_id"))
        return {"sensitivity_table": None}

    threshold = thresholds[0]  # use the most prominent threshold
    original_value = threshold["value"]
    low_value = original_value * 0.5
    high_value = original_value * 2.0
    original_sql = sql_list[0]

    from app.services.agents.redshift_client import execute_query

    async def _run_probe(probe_value: float) -> int | None:
        probe_sql = _build_probe_sql(original_sql, threshold, probe_value)
        if not probe_sql:
            return None
        try:
            columns, rows = await execute_query(probe_sql, timeout_s=15, thread_id=state.get("thread_id", ""))
            if rows and rows[0]:
                return int(rows[0][0]) if rows[0][0] is not None else None
        except Exception as e:
            logger.warning("deep_sensitivity | probe failed | value={} | error={}", probe_value, e)
        return None

    # Get current result count from existing result_list
    result_list = state.get("result_list") or []
    current_count = sum(len(r.get("rows") or []) for r in result_list)

    low_count = await _run_probe(low_value)
    high_count = await _run_probe(high_value)

    if low_count is None and high_count is None:
        logger.info("deep_sensitivity | all probes failed | thread={}", state.get("thread_id"))
        return {"sensitivity_table": None}

    def _fmt_value(v: float) -> str:
        if v >= 1_000_000_000:
            return f"${v/1_000_000_000:.1f}B"
        if v >= 1_000_000:
            return f"${v/1_000_000:.1f}M"
        if v >= 1_000:
            return f"${v/1_000:.0f}K"
        return f"${v:,.0f}"

    rows_out: list[dict] = []
    if low_count is not None:
        rows_out.append({
            "threshold_label": f"{threshold['operator']} {_fmt_value(low_value)} (half)",
            "threshold_value": low_value,
            "count": low_count,
            "is_current": False,
        })
    rows_out.append({
        "threshold_label": f"{threshold['operator']} {_fmt_value(original_value)} ← your threshold",
        "threshold_value": original_value,
        "count": current_count,
        "is_current": True,
    })
    if high_count is not None:
        rows_out.append({
            "threshold_label": f"{threshold['operator']} {_fmt_value(high_value)} (double)",
            "threshold_value": high_value,
            "count": high_count,
            "is_current": False,
        })

    logger.info(
        "deep_sensitivity | done | thread={} | threshold={} | variants={}",
        state.get("thread_id"), original_value, len(rows_out),
    )
    return {"sensitivity_table": rows_out}

"""Deep analysis node: temporal projection — "at this pace..."

Runs only when deep_analysis=True, the result is a scalar/small aggregate, and the
time filter covers a period that is still in-flight (end date >= today).
Computes the current completeness %, projects the end-of-period total, and fetches
the same metric at the equivalent point in the prior period for comparison.
Non-fatal: returns None on any failure.
"""

from __future__ import annotations
import datetime
import re

from app.core.logger import logger
from app.services.agents.state import AnalyticsState


def _parse_date(s: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _is_partial_period(state: AnalyticsState) -> tuple[bool, datetime.date | None, datetime.date | None, float | None]:
    """Return (is_partial, period_start, period_end, completeness_pct)."""
    ir_list = state.get("semantic_ir_list") or []
    if not ir_list:
        return False, None, None, None
    ir = ir_list[0]
    tf = ir.get("time_filter") or {}
    val = tf.get("value")

    if not isinstance(val, list) or len(val) < 2:
        return False, None, None, None

    start = _parse_date(val[0])
    end = _parse_date(val[1])
    if not start or not end:
        return False, None, None, None

    current_date_str = state.get("current_date") or datetime.date.today().isoformat()
    today = _parse_date(current_date_str) or datetime.date.today()

    if end < today:
        return False, None, None, None

    period_days = (end - start).days + 1
    elapsed_days = min((today - start).days + 1, period_days)
    if elapsed_days <= 0 or period_days <= 0:
        return False, None, None, None

    pct = elapsed_days / period_days
    return True, start, end, pct


def _shift_sql_dates(sql: str, start: datetime.date, end: datetime.date,
                     new_start: datetime.date, new_end: datetime.date) -> str:
    """Replace the period's start/end date strings in the SQL for the prior period."""
    result = sql
    result = result.replace(start.isoformat(), new_start.isoformat())
    result = result.replace(end.isoformat(), new_end.isoformat())
    return result


def _prior_period_dates(start: datetime.date, end: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Compute the equivalent prior period (same length, immediately preceding)."""
    delta = (end - start).days + 1
    prior_end = start - datetime.timedelta(days=1)
    prior_start = prior_end - datetime.timedelta(days=delta - 1)
    return prior_start, prior_end


def _prior_period_equivalent_dates(
    start: datetime.date, end: datetime.date, today: datetime.date
) -> tuple[datetime.date, datetime.date]:
    """Compute the equivalent elapsed-portion of the prior period (for same-point comparison)."""
    elapsed = (today - start).days
    delta = (end - start).days + 1
    prior_end = start - datetime.timedelta(days=1)
    prior_start = prior_end - datetime.timedelta(days=delta - 1)
    prior_equiv_end = prior_start + datetime.timedelta(days=elapsed)
    return prior_start, min(prior_equiv_end, prior_end)


def _extract_scalar(rows: list, columns: list) -> float | None:
    """Extract the first numeric value from a result set."""
    for row in rows:
        cells = row if isinstance(row, (list, tuple)) else [row]
        for cell in cells:
            try:
                return float(cell)
            except (TypeError, ValueError):
                continue
    return None


async def deep_projection(state: AnalyticsState) -> dict:
    if not state.get("deep_analysis"):
        return {"temporal_projection": None}

    result_list = state.get("result_list") or []
    total_rows = sum(len(r.get("rows") or []) for r in result_list)
    if total_rows == 0 or total_rows > 5:
        return {"temporal_projection": None}

    is_partial, period_start, period_end, completeness_pct = _is_partial_period(state)
    if not is_partial or completeness_pct is None or completeness_pct <= 0:
        return {"temporal_projection": None}

    sql_list = state.get("sql_list") or []
    if not sql_list or not sql_list[0]:
        return {"temporal_projection": None}

    original_sql = sql_list[0]
    current_date_str = state.get("current_date") or datetime.date.today().isoformat()
    today = _parse_date(current_date_str) or datetime.date.today()

    # Compute prior period full range and equivalent elapsed range
    prior_full_start, prior_full_end = _prior_period_dates(period_start, period_end)
    prior_equiv_start, prior_equiv_end = _prior_period_equivalent_dates(period_start, period_end, today)

    # Get current total from existing results
    current_total: float | None = None
    for res in result_list:
        rows = res.get("rows") or []
        cols = res.get("columns") or []
        val = _extract_scalar(rows, cols)
        if val is not None:
            current_total = val
            break

    if current_total is None:
        return {"temporal_projection": None}

    projected_total = current_total / completeness_pct

    from app.services.agents.redshift_client import execute_query

    async def _run_sql(sql: str) -> float | None:
        try:
            columns, rows = await execute_query(sql.strip(), timeout_s=15, thread_id=state.get("thread_id", ""))
            return _extract_scalar(rows, columns)
        except Exception as e:
            logger.warning("deep_projection | SQL failed | error={}", e)
            return None

    # Fetch prior period at equivalent elapsed point
    prior_equiv_sql = _shift_sql_dates(original_sql, period_start, period_end, prior_equiv_start, prior_equiv_end)
    prior_at_same_point = None
    if prior_equiv_sql != original_sql:
        prior_at_same_point = await _run_sql(prior_equiv_sql)

    # Fetch prior period final total
    prior_full_sql = _shift_sql_dates(original_sql, period_start, period_end, prior_full_start, prior_full_end)
    prior_final = None
    if prior_full_sql != original_sql:
        prior_final = await _run_sql(prior_full_sql)

    if prior_at_same_point is None and prior_final is None:
        # Still useful to surface projection even without prior period data
        pass

    logger.info(
        "deep_projection | done | thread={} | completeness={:.0%} | projected={} | prior_equiv={} | prior_final={}",
        state.get("thread_id"), completeness_pct, projected_total, prior_at_same_point, prior_final,
    )

    return {
        "temporal_projection": {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "completeness_pct": round(completeness_pct * 100, 1),
            "current_total": current_total,
            "projected_total": projected_total,
            "prior_period_start": prior_full_start.isoformat(),
            "prior_period_end": prior_full_end.isoformat(),
            "prior_period_at_same_point": prior_at_same_point,
            "prior_period_final": prior_final,
        }
    }

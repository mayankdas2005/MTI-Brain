"""Deep analysis node: invisible denominator — "but $42M out of what?"

Runs only when deep_analysis=True and the result is a single aggregate metric.
Uses a Haiku call to identify the natural denominator concept (e.g., "total outbound
payments") and generate a denominator SQL, then executes it to compute the share.
Non-fatal: returns None on any failure and pipeline continues normally.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.state import AnalyticsState

_DENOMINATOR_PROMPT = """You are a financial data analyst. Given a specific filtered metric and its source tables, identify the natural denominator that would give this metric meaningful context as a percentage.

QUESTION: {question}

METRIC COMPUTED: {metric_description}
ANCHOR TABLES: {anchor_tables}
TIME FILTER USED: {time_filter}

Your task:
1. Identify the natural "total" that this metric is a subset of.
   Examples: wire transfers → total outbound payments; active accounts → all accounts; FX transactions → all transactions
2. Write a single SQL SELECT query that computes this denominator total using the same time filter.
   The query must use only the anchor tables listed above.
   Use COUNT(*) or SUM(same_column) as appropriate.
   Apply the same time filter as the original query.
   Keep it simple — one CTE or direct SELECT.
3. Name the denominator concept in plain English (e.g., "total outbound payments", "all accounts").

Respond inside <denominator> tags only. If you cannot identify a meaningful denominator, respond with <denominator>null</denominator>.

<denominator>
{{
  "concept": "total outbound payments",
  "sql": "SELECT COUNT(*) AS total_count FROM schema.transactions WHERE value_date BETWEEN '2026-06-01' AND '2026-06-30'"
}}
</denominator>"""


def _build_metric_description(state: AnalyticsState) -> str:
    ir_list = state.get("semantic_ir_list") or []
    if not ir_list:
        return state.get("question", "")
    ir = ir_list[0]
    measures = ir.get("measures") or []
    if measures:
        m = measures[0]
        agg = m.get("aggregation") or "SELECT"
        col = m.get("column_name") or "value"
        alias = m.get("alias") or col
        return f"{agg}({col}) as {alias}"
    return state.get("question", "")


def _get_time_filter_description(state: AnalyticsState) -> str:
    ir_list = state.get("semantic_ir_list") or []
    if not ir_list:
        return "(no time filter)"
    ir = ir_list[0]
    tf = ir.get("time_filter") or {}
    val = tf.get("value")
    col = tf.get("column_name") or "date column"
    if isinstance(val, list) and len(val) == 2:
        return f"{col} BETWEEN '{val[0]}' AND '{val[1]}'"
    if isinstance(val, str):
        return f"{col} {tf.get('operator', '=')} '{val}'"
    return state.get("filter_directive", "(no time filter)")[:200]


def _is_single_aggregate(state: AnalyticsState) -> bool:
    """Return True only for scalar/simple aggregate results (1-3 rows)."""
    result_list = state.get("result_list") or []
    total_rows = sum(len(r.get("rows") or []) for r in result_list)
    return 1 <= total_rows <= 3 and not state.get("no_data")


async def deep_denominator(state: AnalyticsState) -> dict:
    if not state.get("deep_analysis"):
        return {"denominator_context": None}

    if not _is_single_aggregate(state):
        return {"denominator_context": None}

    ir_list = state.get("semantic_ir_list") or []
    anchor_tables = ir_list[0].get("anchor_tables", []) if ir_list else []
    if not anchor_tables:
        return {"denominator_context": None}

    metric_desc = _build_metric_description(state)
    time_filter_desc = _get_time_filter_description(state)

    prompt_text = _DENOMINATOR_PROMPT.format(
        question=state.get("question", ""),
        metric_description=metric_desc,
        anchor_tables=", ".join(anchor_tables),
        time_filter=time_filter_desc,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from app.core.retry import retry_async
    import json_repair

    haiku = get_llm("fast")

    try:
        @llm_breaker
        async def _call():
            from langchain_core.messages import HumanMessage
            return await retry_async(
                lambda: haiku.ainvoke([HumanMessage(content=prompt_text)]),
                service="bedrock-deep-denominator",
                max_attempts=2,
                backoff_base=3.0,
            )
        response = await _call()
        raw = response.content or ""
        tag_content = parse_tag(raw, "denominator") or ""
        tag_content = tag_content.strip()
        if not tag_content or tag_content.lower() == "null":
            return {"denominator_context": None}

        parsed = json_repair.loads(tag_content)
        if not isinstance(parsed, dict):
            return {"denominator_context": None}

        concept = parsed.get("concept", "")
        denom_sql = parsed.get("sql", "")
        if not concept or not denom_sql:
            return {"denominator_context": None}

    except Exception as e:
        logger.warning("deep_denominator | LLM call failed | thread={} | error={}", state.get("thread_id"), e)
        return {"denominator_context": None}

    # Execute the denominator SQL
    from app.services.agents.redshift_client import execute_query

    try:
        columns, rows = await execute_query(denom_sql.strip(), timeout_s=15, thread_id=state.get("thread_id", ""))
        if not rows or not rows[0]:
            return {"denominator_context": None}
        denom_value = rows[0][0]
        if denom_value is None:
            return {"denominator_context": None}
        denom_float = float(denom_value)
        if denom_float == 0:
            return {"denominator_context": None}
    except Exception as e:
        logger.warning("deep_denominator | SQL execution failed | thread={} | error={}", state.get("thread_id"), e)
        return {"denominator_context": None}

    # Compute share from first result row's first numeric column
    result_list = state.get("result_list") or []
    current_value: float | None = None
    for res in result_list:
        for row in (res.get("rows") or []):
            for cell in (row if isinstance(row, list) else []):
                try:
                    current_value = float(cell)
                    break
                except (TypeError, ValueError):
                    continue
            if current_value is not None:
                break
        if current_value is not None:
            break

    share = round(current_value / denom_float, 4) if current_value is not None else None

    logger.info(
        "deep_denominator | done | thread={} | concept={} | denom={} | share={}",
        state.get("thread_id"), concept, denom_float, share,
    )

    return {
        "denominator_context": {
            "concept": concept,
            "value": denom_float,
            "share": share,
        }
    }

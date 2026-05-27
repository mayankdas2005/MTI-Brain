"""Node 5: chart_agent — selects chart type and generates Vega-Lite labels.

Step 1: LLM selects chart type AND generates labels in one call (semantic, persona-aware).
Step 2: Hard safety guardrails applied after LLM choice (single row → kpi_card, etc.).
Step 3: Programmatic Vega-Lite spec builder injects fixed fields and data rows.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.prompts import CHART_LABEL_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.state import AnalyticsState

_VALID_CHART_TYPES = {
    "kpi_card", "bar", "bar_horizontal", "line", "area", "multi_line",
    "stacked_area", "pie", "donut", "grouped_bar", "stacked_bar",
    "scatter", "bubble", "heatmap", "waterfall", "dual_axis", "table",
}


async def chart_agent(state: AnalyticsState, config: RunnableConfig) -> dict:
    query_summary = state.get("query_summary") or {}
    result_list = state.get("result_list") or []
    ir_list = state.get("semantic_ir_list") or []

    all_columns: list[str] = query_summary.get("columns") and [c["name"] for c in query_summary["columns"]] or []
    all_rows: list[list] = []
    for res in result_list:
        if res.get("rows"):
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            all_rows.extend(res["rows"])

    if not all_columns or not all_rows:
        logger.info("chart_agent | no data for chart | thread={}", state["thread_id"])
        return {"chart_spec": None}

    intent = ir_list[0].get("intent", "") if ir_list else ""
    persona = state.get("persona", "executive")

    chart_type, labels = await _select_chart_and_labels(
        all_columns, all_rows, query_summary, state, config, intent, persona
    )

    # Safety guardrails that override LLM choice
    chart_type = _apply_guardrails(chart_type, all_columns, all_rows)

    logger.info("chart_agent | selected type={} | thread={} | rows={}", chart_type, state["thread_id"], len(all_rows))

    if chart_type == "table":
        spec = _build_table_spec(all_columns, all_rows)
        return {"chart_spec": spec}

    spec = _build_vega_lite_spec(chart_type, all_columns, all_rows, labels)
    logger.info("chart_agent DONE | thread={} | type={}", state["thread_id"], chart_type)
    return {"chart_spec": spec}


def _apply_guardrails(chart_type: str, columns: list[str], rows: list[list]) -> str:
    """Hard rules that override LLM choice regardless of reasoning."""
    n_rows = len(rows)
    col_types = _classify_column_types(columns, rows)
    n_numeric = col_types.count("numeric")
    n_date = col_types.count("date")

    if n_rows == 1 and n_numeric >= 1:
        return "kpi_card"

    if n_rows <= 5 and n_numeric == len(columns):
        return "kpi_card"

    if chart_type in ("line", "area", "multi_line", "stacked_area") and n_date == 0:
        return "bar"

    if chart_type == "scatter" and (n_numeric < 2 or n_date > 0):
        return "bar"

    if chart_type not in _VALID_CHART_TYPES:
        return "table"

    return chart_type


async def _select_chart_and_labels(
    columns: list[str],
    rows: list[list],
    query_summary: dict,
    state: AnalyticsState,
    config: RunnableConfig,
    intent: str,
    persona: str,
) -> tuple[str, dict]:
    col_stats_text = ""
    if query_summary.get("columns"):
        col_stats_text = " | ".join(
            f"{c['name']}({c.get('dtype', '?')})"
            for c in query_summary["columns"][:8]
        )
    else:
        col_types = _classify_column_types(columns, rows)
        col_stats_text = " | ".join(f"{col}({t})" for col, t in zip(columns[:8], col_types))

    sample_rows = [dict(zip(columns, row)) for row in rows[:5]]

    prompt = CHART_LABEL_PROMPT.format_messages(
        question=state["question"],
        intent=intent,
        persona=persona,
        column_stats=col_stats_text,
        row_count=len(rows),
        sample_rows=json.dumps(sample_rows, default=str),
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    fallback_type = _fallback_chart_type(columns, rows, intent, persona)
    fallback_labels = {
        "chart_type": fallback_type,
        "chart_title": state["question"][:60],
        "x_axis_label": columns[0] if columns else "x",
        "y_axis_label": columns[1] if len(columns) > 1 else "value",
        "legend_labels": {},
        "value_format": ",.0f",
        "color_scheme": "blues",
    }

    try:
        response = await _call()
        raw = response.content or ""
        output = parse_tag(raw, "chart")
        if output:
            from json_repair import loads as json_loads
            parsed = json_loads(output)
            if isinstance(parsed, dict):
                chart_type = parsed.get("chart_type", fallback_type)
                return chart_type, parsed
    except Exception as e:
        logger.warning("chart_agent | LLM selection failed | error={}", e)

    return fallback_type, fallback_labels


def _fallback_chart_type(columns: list[str], rows: list[list], intent: str, persona: str) -> str:
    """Simple deterministic fallback used only when LLM call fails."""
    if not rows or not columns:
        return "table"
    n_rows = len(rows)
    col_types = _classify_column_types(columns, rows)
    n_numeric = col_types.count("numeric")
    n_date = col_types.count("date")
    n_string = col_types.count("string")

    if n_rows == 1 and n_numeric >= 1:
        return "kpi_card"
    if n_rows <= 5 and n_numeric == len(columns):
        return "kpi_card"
    if n_date >= 1:
        return "area" if persona in ("executive", "director") else "line"
    if n_string == 1 and n_numeric == 1:
        return "bar" if n_rows > 5 else ("donut" if persona in ("executive", "director") else "pie")
    if n_rows > 30:
        return "table"
    return "bar"


def _build_vega_lite_spec(chart_type: str, columns: list[str], rows: list[list], labels: dict) -> dict:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": "container",
        "height": 350,
        "background": "transparent",
        "title": labels.get("chart_title", ""),
        "data": {"values": [dict(zip(columns, row)) for row in rows[:500]]},
    }

    if chart_type in ("bar", "bar_horizontal"):
        y_col = _find_numeric_col(columns, rows)
        x_col = _find_string_col(columns, rows, exclude=y_col) or (columns[0] if columns else "x")
        spec["mark"] = "bar"
        spec["encoding"] = {
            "x" if chart_type == "bar" else "y": {
                "field": x_col, "type": "nominal",
                "axis": {"title": labels.get("x_axis_label", x_col)},
            },
            "y" if chart_type == "bar" else "x": {
                "field": y_col, "type": "quantitative",
                "axis": {"title": labels.get("y_axis_label", y_col), "format": labels.get("value_format", ",.0f")},
            },
        }

    elif chart_type in ("line", "area"):
        x_col = _find_date_col(columns, rows)
        y_col = _find_numeric_col(columns, rows, exclude=x_col)
        spec["mark"] = chart_type
        spec["encoding"] = {
            "x": {"field": x_col, "type": "temporal", "timeUnit": "yearmonthdate", "axis": {"title": labels.get("x_axis_label", x_col), "format": "%b %d, %Y"}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col), "format": labels.get("value_format", ",.0f")}},
        }

    elif chart_type in ("multi_line", "stacked_area"):
        x_col = _find_date_col(columns, rows)
        cat_col = _find_string_col(columns, rows, exclude=x_col)
        y_col = _find_numeric_col(columns, rows, exclude=x_col)
        mark = "line" if chart_type == "multi_line" else "area"
        spec["mark"] = mark
        encoding: dict = {
            "x": {"field": x_col, "type": "temporal", "timeUnit": "yearmonthdate", "axis": {"title": labels.get("x_axis_label", x_col), "format": "%b %d, %Y"}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col)}},
            "color": {"field": cat_col, "type": "nominal"},
        }
        if chart_type == "stacked_area":
            encoding["y"]["stack"] = "zero"
        spec["encoding"] = encoding

    elif chart_type in ("pie", "donut"):
        name_col = _find_string_col(columns, rows)
        value_col = _find_numeric_col(columns, rows)
        spec["mark"] = {"type": "arc", "innerRadius": 50 if chart_type == "donut" else 0}
        spec["encoding"] = {
            "theta": {"field": value_col, "type": "quantitative"},
            "color": {"field": name_col, "type": "nominal"},
        }

    elif chart_type == "kpi_card":
        spec["type"] = "kpi_card"
        spec["values"] = [dict(zip(columns, row)) for row in rows[:3]]
        return spec

    elif chart_type in ("grouped_bar", "stacked_bar"):
        string_cols = [c for i, c in enumerate(columns) if _classify_column_types(columns, rows)[i] == "string"]
        x_col = string_cols[0] if string_cols else columns[0]
        cat_col = string_cols[1] if len(string_cols) > 1 else (string_cols[0] if string_cols else columns[0])
        y_col = _find_numeric_col(columns, rows)
        spec["mark"] = "bar"
        encoding = {
            "x": {"field": x_col, "type": "nominal", "axis": {"title": labels.get("x_axis_label", x_col)}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col), "format": labels.get("value_format", ",.0f")}},
            "color": {"field": cat_col, "type": "nominal"},
        }
        if chart_type == "stacked_bar":
            encoding["y"]["stack"] = "zero"
        else:
            encoding["xOffset"] = {"field": cat_col}
        spec["encoding"] = encoding

    elif chart_type == "scatter":
        x_col = _find_numeric_col(columns, rows)
        y_col = _find_numeric_col(columns, rows, exclude=x_col)
        spec["mark"] = "point"
        spec["encoding"] = {
            "x": {"field": x_col, "type": "quantitative", "axis": {"title": labels.get("x_axis_label", x_col)}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col)}},
        }

    else:
        spec["mark"] = "bar"
        if columns:
            spec["encoding"] = {
                "x": {"field": columns[0], "type": "nominal"},
                "y": {"field": columns[1] if len(columns) > 1 else columns[0], "type": "quantitative"},
            }

    spec["config"] = {
        "axis": {"labelFont": "Inter, sans-serif", "titleFont": "Inter, sans-serif"},
        "legend": {"labelFont": "Inter, sans-serif"},
    }
    return spec


def _build_table_spec(columns: list[str], rows: list[list]) -> dict:
    return {
        "type": "table",
        "columns": columns,
        "rows": [list(r) for r in rows[:200]],
    }


def _classify_column_types(columns: list[str], rows: list[list]) -> list[str]:
    import re
    result = []
    for i in range(len(columns)):
        sample = [rows[j][i] for j in range(min(20, len(rows))) if rows[j][i] is not None]
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


def _find_date_col(columns: list[str], rows: list[list]) -> str:
    import re
    for i, col in enumerate(columns):
        sample = [str(rows[j][i]) for j in range(min(5, len(rows))) if rows[j][i] is not None]
        if sample and re.match(r"\d{4}-\d{2}-\d{2}", sample[0]):
            return col
    return columns[0] if columns else "date"


def _find_numeric_col(columns: list[str], rows: list[list], exclude: str | None = None) -> str:
    for i, col in enumerate(columns):
        if col == exclude:
            continue
        sample = [rows[j][i] for j in range(min(5, len(rows))) if rows[j][i] is not None]
        try:
            [float(v) for v in sample]
            return col
        except (TypeError, ValueError):
            pass
    return columns[-1] if columns else "value"


def _find_string_col(columns: list[str], rows: list[list], exclude: str | None = None) -> str:
    for i, col in enumerate(columns):
        if col == exclude:
            continue
        sample = [rows[j][i] for j in range(min(5, len(rows))) if rows[j][i] is not None]
        try:
            [float(v) for v in sample]
        except (TypeError, ValueError):
            return col
    return columns[0] if columns else "category"

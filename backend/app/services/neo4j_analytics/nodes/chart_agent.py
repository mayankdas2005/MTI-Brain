"""Node 5: chart_agent — selects chart type and generates Vega-Lite labels.

Step 1: Deterministic chart type selection (chart_rules.py).
Step 2: Haiku LLM generates text labels only.
Step 3: Programmatic Vega-Lite spec builder injects fixed fields and data rows.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.chart_rules import select_chart_type
from app.services.neo4j_analytics.prompts import CHART_LABEL_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.state import AnalyticsState


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

    result_shape = query_summary.get("result_shape", "flat")
    intent = ir_list[0].get("intent", "") if ir_list else ""
    persona = state.get("persona", "executive")
    chart_type = select_chart_type(all_columns, all_rows, result_shape, intent, persona)

    logger.info("chart_agent | selected type={} | thread={} | rows={}", chart_type, state["thread_id"], len(all_rows))

    if chart_type == "table":
        spec = _build_table_spec(all_columns, all_rows)
        return {"chart_spec": spec}

    labels = await _generate_labels(chart_type, all_columns, query_summary, state, config, intent)
    spec = _build_vega_lite_spec(chart_type, all_columns, all_rows, labels)

    logger.info("chart_agent DONE | thread={} | type={}", state["thread_id"], chart_type)
    return {"chart_spec": spec}


async def _generate_labels(
    chart_type: str,
    columns: list[str],
    query_summary: dict,
    state: AnalyticsState,
    config: RunnableConfig,
    intent: str,
) -> dict:
    col_stats_text = ""
    if query_summary.get("columns"):
        col_stats_text = " | ".join(
            f"{c['name']}({c.get('dtype', '?')})"
            for c in query_summary["columns"][:6]
        )

    prompt = CHART_LABEL_PROMPT.format_messages(
        chart_type=chart_type,
        column_names=", ".join(columns[:8]),
        column_stats=col_stats_text,
        question=state["question"],
        intent=intent,
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    try:
        response = await _call()
        raw = response.content or ""
        output = parse_tag(raw, "chart")
        if output:
            from json_repair import loads as json_loads
            return json_loads(output)
    except Exception as e:
        logger.warning("chart_agent | label generation failed | error={}", e)

    return {
        "chart_title": state["question"][:60],
        "x_axis_label": columns[0] if columns else "x",
        "y_axis_label": columns[1] if len(columns) > 1 else "value",
        "legend_labels": {},
        "value_format": ",.0f",
        "color_scheme": "blues",
    }


def _build_vega_lite_spec(chart_type: str, columns: list[str], rows: list[list], labels: dict) -> dict:
    """Build a Vega-Lite spec from chart type, data, and labels."""
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": "container",
        "height": 350,
        "background": "transparent",
        "title": labels.get("chart_title", ""),
        "data": {"values": [dict(zip(columns, row)) for row in rows[:500]]},
    }

    if chart_type in ("bar", "bar_horizontal"):
        x_col = columns[0] if columns else "x"
        y_col = columns[1] if len(columns) > 1 else "y"
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
            "x": {"field": x_col, "type": "temporal", "axis": {"title": labels.get("x_axis_label", x_col)}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col), "format": labels.get("value_format", ",.0f")}},
        }

    elif chart_type == "multi_line":
        x_col = _find_date_col(columns, rows)
        cat_col = _find_string_col(columns, rows, exclude=x_col)
        y_col = _find_numeric_col(columns, rows, exclude=x_col)
        spec["mark"] = "line"
        spec["encoding"] = {
            "x": {"field": x_col, "type": "temporal", "axis": {"title": labels.get("x_axis_label", x_col)}},
            "y": {"field": y_col, "type": "quantitative", "axis": {"title": labels.get("y_axis_label", y_col)}},
            "color": {"field": cat_col, "type": "nominal"},
        }

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

    elif chart_type == "scatter":
        x_col = _find_numeric_col(columns, rows)
        y_col = _find_numeric_col(columns, rows, exclude=x_col)
        spec["mark"] = "point"
        spec["encoding"] = {
            "x": {"field": x_col, "type": "quantitative"},
            "y": {"field": y_col, "type": "quantitative"},
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


def _find_date_col(columns: list[str], rows: list[list]) -> str:
    import re
    for i, col in enumerate(columns):
        sample = [str(r[i]) for r in rows[:5] if r[i] is not None]
        if sample and re.match(r"\d{4}-\d{2}-\d{2}", sample[0]):
            return col
    return columns[0] if columns else "date"


def _find_numeric_col(columns: list[str], rows: list[list], exclude: str | None = None) -> str:
    for i, col in enumerate(columns):
        if col == exclude:
            continue
        sample = [r[i] for r in rows[:5] if r[i] is not None]
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
        sample = [r[i] for r in rows[:5] if r[i] is not None]
        try:
            [float(v) for v in sample]
        except (TypeError, ValueError):
            return col
    return columns[0] if columns else "category"

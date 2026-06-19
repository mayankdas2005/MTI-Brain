"""Node 5: chart_agent — LLM-driven chart specification.

Single LLM call decides: chart type, column bindings, x_column_type, labels, sort order.
Python handles: data cleaning, row sorting, Vega-Lite spec assembly, large-number formatting.
"""

from __future__ import annotations
import copy
import re
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import _build_data_profile, build_mission_context, parse_tag
from app.services.agents.prompts import CHART_AGENT_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState

_VALID_CHART_TYPES = {
    "kpi_card", "bar", "grouped_bar", "line",
    "donut", "scatter", "heatmap", "waterfall",
}

_WATERFALL_MAX_ROWS = 20
_MAX_BAR_CATS = 25

_FONT = "Inter, sans-serif"

_BASE_CONFIG = {
    "axis":   {"labelFont": _FONT, "titleFont": _FONT, "labelFontSize": 11, "titleFontSize": 11},
    "legend": {"labelFont": _FONT, "titleFont": _FONT, "labelFontSize": 11},
    "title":  {"font": _FONT, "fontSize": 13, "fontWeight": 500},
    "bar":    {"strokeWidth": 0, "cornerRadiusTopLeft": 2, "cornerRadiusTopRight": 2},
    "arc":    {"strokeWidth": 1, "stroke": "white"},
    "line":   {"strokeWidth": 2},
    "area":   {"strokeWidth": 0, "fillOpacity": 0.75},
    "point":  {"strokeWidth": 0, "size": 60},
}


# ─── Data cleaning ────────────────────────────────────────────────────────────

def _drop_blank_rows(columns: list[str], rows: list[list]) -> list[list]:
    import math
    if not rows or not columns:
        return rows
    def _blank(v):
        if v is None: return True
        if isinstance(v, float) and math.isnan(v): return True
        return v in (0, 0.0, "", "0", "-")
    n = len(columns)
    return [row for row in rows if any(not _blank(row[i]) for i in range(min(n, len(row))))]


def _drop_blank_columns(columns: list[str], rows: list[list]) -> tuple[list[str], list[list]]:
    import math
    if not rows or not columns:
        return columns, rows
    def _blank(v):
        if v is None: return True
        if isinstance(v, float) and math.isnan(v): return True
        return v in (0, 0.0, "", "0", "-")
    keep = [i for i, _ in enumerate(columns) if any(not _blank(row[i]) for row in rows if i < len(row))]
    if len(keep) == len(columns):
        return columns, rows
    return [columns[i] for i in keep], [[row[i] for i in keep if i < len(row)] for row in rows]


# ─── SQL column source extraction (sqlglot) ──────────────────────────────────

def _extract_sql_column_sources(sql: str) -> dict[str, tuple[str, str]]:
    try:
        import sqlglot
        from sqlglot import exp as sg
    except ImportError:
        return {}
    try:
        stmt = sqlglot.parse_one(sql, read="redshift")
    except Exception:
        return {}

    tbl_map: dict[str, str] = {}
    for tbl in stmt.find_all(sg.Table):
        if not tbl.name:
            continue
        fqn = f"{tbl.db}.{tbl.name}" if tbl.db else tbl.name
        for key in filter(None, {tbl.name.lower(), (tbl.alias or tbl.name).lower()}):
            tbl_map[key] = fqn

    cte_map: dict[str, sg.Select] = {}
    for cte in stmt.find_all(sg.CTE):
        cte_map[cte.alias.lower()] = cte.this

    _AGG = (sg.Avg, sg.Sum, sg.Count, sg.Min, sg.Max)

    def _resolve(expr, ctx_sel=None, depth: int = 0):
        if depth > 8 or expr is None:
            return None
        if isinstance(expr, (sg.Alias, sg.Cast, sg.Paren)):
            return _resolve(expr.this, ctx_sel, depth)
        if isinstance(expr, _AGG):
            return _resolve(expr.this, ctx_sel, depth + 1)
        if isinstance(expr, sg.Column):
            col = expr.name
            tbl = (expr.table or "").lower()
            if tbl:
                if tbl in cte_map:
                    return _find_in_cte(col, cte_map[tbl], depth + 1)
                return (tbl_map.get(tbl, tbl), col)
            if ctx_sel:
                return _find_in_from(col, ctx_sel, depth + 1)
        return None

    def _find_in_cte(col: str, cte_sel, depth: int):
        if depth > 8:
            return None
        for s in cte_sel.selects:
            a = s.alias if isinstance(s, sg.Alias) else (s.name if isinstance(s, sg.Column) else None)
            if a and a.lower() == col.lower():
                inner = s.this if isinstance(s, sg.Alias) else s
                return _resolve(inner, cte_sel, depth + 1)
        return None

    def _find_in_from(col: str, sel, depth: int):
        if depth > 8:
            return None
        from_clause = sel.args.get("from_")
        joins = sel.args.get("joins") or []
        sources = ([from_clause.this] if from_clause else []) + [j.this for j in joins]
        for src in sources:
            if isinstance(src, sg.Table):
                key = (src.alias or src.name or "").lower()
                if key in cte_map:
                    r = _find_in_cte(col, cte_map[key], depth + 1)
                    if r:
                        return r
                elif key in tbl_map:
                    return (tbl_map[key], col)
        return None

    def _expand_select(sel, depth: int = 0) -> dict[str, tuple[str, str]]:
        if depth > 4:
            return {}
        out: dict[str, tuple[str, str]] = {}
        for s in sel.selects:
            if isinstance(s, sg.Star):
                from_clause = sel.args.get("from_")
                if from_clause:
                    tbl_node = from_clause.find(sg.Table)
                    if tbl_node:
                        key = (tbl_node.alias or tbl_node.name or "").lower()
                        if key in cte_map:
                            out.update(_expand_select(cte_map[key], depth + 1))
                continue
            alias = s.alias if isinstance(s, sg.Alias) else (s.name if isinstance(s, sg.Column) else None)
            if not alias:
                continue
            inner = s.this if isinstance(s, sg.Alias) else s
            src = _resolve(inner, sel)
            if src:
                out[alias] = src
        return out

    try:
        return _expand_select(stmt)
    except Exception:
        return {}


def _build_column_metadata(columns: list[str], state: AnalyticsState) -> str:
    sql = (state.get("sql_list") or [""])[0]
    col_sources: dict[str, tuple[str, str]] = _extract_sql_column_sources(sql) if sql else {}

    ir = (state.get("semantic_ir_list") or [{}])[0]
    for item in list(ir.get("measures") or []) + list(ir.get("dimensions") or []):
        alias = item.get("alias") or item.get("column_name") or ""
        col_name = item.get("column_name") or ""
        table_fqn = item.get("table_fqn") or ""
        if alias and alias not in col_sources and col_name and table_fqn:
            col_sources[alias] = (table_fqn, col_name)

    semantic_ctx = state.get("semantic_context") or {}
    col_meta: dict[tuple[str, str], dict] = {}
    for c in (semantic_ctx.get("columns") or []):
        key = (c.get("table_fqn") or "", c.get("name") or "")
        if key[0] or key[1]:
            col_meta[key] = c

    lines = ["COLUMN METADATA (semantic type, data type, description — use to pick format and chart type):"]
    for col in columns:
        src = col_sources.get(col)
        meta = col_meta.get(src) if src else None
        sem_type   = (meta.get("semantic_type") or "") if meta else ""
        data_type  = (meta.get("data_type") or "") if meta else ""
        description = (meta.get("description") or "") if meta else ""
        parts: list[str] = []
        if data_type:
            parts.append(f"type={data_type}")
        if sem_type:
            parts.append(f"semantic={sem_type}")
        if description:
            parts.append(f"desc={description[:80]}")
        suffix = "   " + "   ".join(parts) if parts else "   (no catalog entry)"
        lines.append(f"  {col}{suffix}")
    return "\n".join(lines)


# ─── Label humanization ───────────────────────────────────────────────────────

def _snake_to_title(s: str) -> str:
    if not s or not isinstance(s, str):
        return s
    if " " in s.strip():
        return s
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", s) if w)


def _humanize_spec_labels(spec: dict) -> dict:
    spec = copy.deepcopy(spec)
    if isinstance(spec.get("title"), str):
        spec["title"] = _snake_to_title(spec["title"])

    def _humanize_tooltip_list(tooltip_list: list) -> None:
        for item in tooltip_list:
            if not isinstance(item, dict):
                continue
            existing = item.get("title")
            if existing is None:
                field = item.get("field", "")
                if field:
                    item["title"] = _snake_to_title(field)
            elif isinstance(existing, str) and ("_" in existing or existing.isupper()):
                item["title"] = _snake_to_title(existing)

    def _humanize_encoding(encoding: dict) -> None:
        top_tt = encoding.get("tooltip")
        if isinstance(top_tt, list):
            _humanize_tooltip_list(top_tt)
        for ch in encoding.values():
            if not isinstance(ch, dict):
                continue
            axis = ch.get("axis")
            if isinstance(axis, dict) and isinstance(axis.get("title"), str):
                axis["title"] = _snake_to_title(axis["title"])
            legend = ch.get("legend")
            if isinstance(legend, dict) and isinstance(legend.get("title"), str):
                legend["title"] = _snake_to_title(legend["title"])
            tooltip = ch.get("tooltip")
            if isinstance(tooltip, list):
                _humanize_tooltip_list(tooltip)

    encoding = spec.get("encoding")
    if isinstance(encoding, dict):
        _humanize_encoding(encoding)
    for layer in spec.get("layer", []):
        if isinstance(layer, dict):
            layer_enc = layer.get("encoding")
            if isinstance(layer_enc, dict):
                _humanize_encoding(layer_enc)
    return spec


# ─── Large-number axis formatting ─────────────────────────────────────────────

def _extract_currency_symbol(value_format: str) -> str:
    for sym in ("$", "₹", "€", "£", "¥", "₩", "₦", "₱", "₫", "฿"):
        if sym in (value_format or ""):
            return sym
    return ""


def _col_max_abs(rows: list[list], col_idx: int) -> float | None:
    vals: list[float] = []
    for row in rows:
        v = row[col_idx] if col_idx < len(row) else None
        if v is None:
            continue
        try:
            vals.append(abs(float(v)))
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else None


def _col_p99(rows: list[list], col_idx: int) -> float | None:
    vals: list[float] = []
    for row in rows:
        v = row[col_idx] if col_idx < len(row) else None
        if v is None:
            continue
        try:
            vals.append(abs(float(v)))
        except (TypeError, ValueError):
            pass
    if not vals:
        return None
    vals.sort()
    idx = max(0, int(len(vals) * 0.99) - 1)
    return vals[idx]


def _build_label_expr(currency_sym: str = "") -> str:
    sym = currency_sym
    q = "'"
    prefix = f"{q}{sym}{q} + " if sym else ""
    def _tier(divisor: str, suffix: str) -> str:
        fmt = f"{q}.2f{q}"
        return f"{prefix}format(datum.value / {divisor}, {fmt}) + {q}{suffix}{q}"
    pos = (
        f"datum.value >= 1e12 ? {_tier('1e12', 'T')} : "
        f"datum.value >= 1e9  ? {_tier('1e9',  'B')} : "
        f"datum.value >= 1e6  ? {_tier('1e6',  'M')} : "
        f"datum.value >= 1e3  ? {_tier('1e3',  'K')} : "
    )
    neg = (
        f"datum.value <= -1e12 ? {_tier('1e12', 'T')} : "
        f"datum.value <= -1e9  ? {_tier('1e9',  'B')} : "
        f"datum.value <= -1e6  ? {_tier('1e6',  'M')} : "
        f"datum.value <= -1e3  ? {_tier('1e3',  'K')} : "
    )
    fallback = f"{prefix}format(datum.value, {q},.0f{q})"
    return pos + neg + fallback


def _spec_field_values(spec: dict, field: str) -> list[float]:
    """Extract numeric values for a named field from spec['data']['values'].

    The spec data is always the aggregated/plotted data regardless of chart type.
    Using it for axis stats ensures max/p99 reflect what is actually rendered,
    not the raw SQL rows which may be pre-aggregation and differ in magnitude.
    """
    data = spec.get("data", {}).get("values")
    if not isinstance(data, list):
        return []
    vals: list[float] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        v = row.get(field)
        if v is None:
            continue
        try:
            vals.append(abs(float(v)))
        except (TypeError, ValueError):
            pass
    return vals


def _fix_large_number_axes(spec: dict, columns: list[str], rows: list[list], plan: dict) -> dict:
    encoding = spec.get("encoding")
    if not isinstance(encoding, dict) or not rows:
        return spec
    spec = copy.deepcopy(spec)
    encoding = spec["encoding"]

    # Global max from raw rows (used only when field is not in spec data)
    global_numeric_max: float = 0.0
    for i in range(len(columns)):
        v = _col_max_abs(rows, i)
        if v is not None and v > global_numeric_max:
            global_numeric_max = v

    for channel in ("y", "x"):
        ch = encoding.get(channel)
        if not isinstance(ch, dict) or ch.get("type") != "quantitative":
            continue
        fmt = plan.get(f"{channel}_value_format") or plan.get("y_value_format") or ""
        currency_sym = _extract_currency_symbol(fmt)
        non_dollar = bool(currency_sym and currency_sym != "$")
        field = ch.get("field")

        # Always derive max/p99 from spec data (aggregated), not raw SQL rows.
        # Raw rows are pre-aggregation; their values differ from what is plotted.
        if isinstance(field, str):
            spec_vals = _spec_field_values(spec, field)
        else:
            spec_vals = []

        if spec_vals:
            max_val: float | None = max(spec_vals)
            spec_vals_sorted = sorted(spec_vals)
            n = len(spec_vals_sorted)
            p99: float | None = spec_vals_sorted[max(0, int(n * 0.99) - 1)] if n >= 2 else None
        elif isinstance(field, str) and field in columns:
            max_val = _col_max_abs(rows, columns.index(field))
            p99 = _col_p99(rows, columns.index(field))
        else:
            max_val = global_numeric_max or None
            p99 = None

        if not non_dollar and (max_val is None or max_val < 1_000_000):
            continue
        axis = ch.get("axis")
        if not isinstance(axis, dict):
            axis = {}
            ch["axis"] = axis
        axis.pop("format", None)
        axis["labelExpr"] = _build_label_expr(currency_sym)
        _title = axis.get("title")
        if isinstance(_title, str) and "%" in _title:
            _cleaned = re.sub(r"\s*\(\s*%\s*\)", "", _title).strip()
            _cleaned = re.sub(r"\s+%$", "", _cleaned).strip()
            if _cleaned != _title:
                axis["title"] = _cleaned
        # Outlier clamp: only meaningful when there are enough plotted data points
        # to compute a reliable p99. With few points (e.g. 2-category bar), every
        # value is meaningful and should never be clipped.
        if p99 and max_val and max_val > 100 * p99 and len(spec_vals) > 10:
            ch.setdefault("scale", {})["domainMax"] = p99 * 1.1
            ch["scale"]["clamp"] = True

    _col_ch = encoding.get("color") if isinstance(encoding, dict) else None
    if isinstance(_col_ch, dict) and _col_ch.get("type") == "quantitative":
        _col_field = _col_ch.get("field")
        _col_max = (_col_max_abs(rows, columns.index(_col_field))
                    if isinstance(_col_field, str) and _col_field in columns
                    else global_numeric_max or None)
        if _col_max and _col_max >= 1_000_000:
            _col_fmt = plan.get("y_value_format") or ""
            _col_csym = _extract_currency_symbol(_col_fmt)
            _col_legend = _col_ch.get("legend")
            if not isinstance(_col_legend, dict):
                _col_legend = {}
                _col_ch["legend"] = _col_legend
            _col_legend.pop("format", None)
            _col_legend["labelExpr"] = _build_label_expr(_col_csym)

    for _layer in (spec.get("layer") or []):
        if not isinstance(_layer, dict):
            continue
        _lenc = _layer.get("encoding")
        if not isinstance(_lenc, dict):
            continue
        for _ch_name in ("y", "x"):
            _lch = _lenc.get(_ch_name)
            if not isinstance(_lch, dict) or _lch.get("type") != "quantitative":
                continue
            _fmt = plan.get(f"{_ch_name}_value_format") or plan.get("y_value_format") or ""
            _csym = _extract_currency_symbol(_fmt)
            _nondollar = bool(_csym and _csym != "$")
            _field = _lch.get("field")
            _mval = (_col_max_abs(rows, columns.index(_field)) if isinstance(_field, str) and _field in columns
                     else global_numeric_max or None)
            if not _nondollar and (_mval is None or _mval < 1_000_000):
                continue
            _lax = _lch.get("axis")
            if not isinstance(_lax, dict):
                _lax = {}
                _lch["axis"] = _lax
            _lax.pop("format", None)
            _lax["labelExpr"] = _build_label_expr(_csym)

    return spec


def _fix_large_number_tooltips(spec: dict, columns: list[str], rows: list[list], plan: dict) -> dict:
    spec = copy.deepcopy(spec)

    # Global fallback: max from spec data first, raw rows second
    spec_all_vals: list[float] = []
    for row in (spec.get("data", {}).get("values") or []):
        if not isinstance(row, dict):
            continue
        for v in row.values():
            if v is None:
                continue
            try:
                spec_all_vals.append(abs(float(v)))
            except (TypeError, ValueError):
                pass
    global_numeric_max: float = max(spec_all_vals) if spec_all_vals else 0.0
    if not global_numeric_max:
        for i in range(len(columns)):
            v = _col_max_abs(rows, i)
            if v is not None and v > global_numeric_max:
                global_numeric_max = v

    encoding = spec.get("encoding") or {}
    field_to_channel: dict[str, str] = {}
    for ch_name in ("x", "y", "size"):
        ch = encoding.get(ch_name)
        if isinstance(ch, dict):
            f = ch.get("field")
            if f:
                field_to_channel[f] = ch_name

    def _patch_tooltip_list(tooltip_list: list) -> None:
        for item in tooltip_list:
            if not isinstance(item, dict) or item.get("type") != "quantitative":
                continue
            field = item.get("field")
            if isinstance(field, str):
                spec_vals = _spec_field_values(spec, field)
                max_val: float | None = max(spec_vals) if spec_vals else None
            else:
                max_val = None
            if max_val is None:
                max_val = global_numeric_max or None
            channel = field_to_channel.get(field or "", "y")
            fmt_str = plan.get(f"{channel}_value_format") or plan.get("y_value_format") or ""
            currency_sym = _extract_currency_symbol(fmt_str)
            non_dollar = bool(currency_sym and currency_sym != "$")
            if not non_dollar and (max_val is None or max_val < 1_000_000):
                continue
            item.pop("format", None)
            item["formatType"] = "smartNum"
            item["format"] = currency_sym or ""

    tt = encoding.get("tooltip")
    if isinstance(tt, list):
        _patch_tooltip_list(tt)
    for layer in spec.get("layer", []):
        if not isinstance(layer, dict):
            continue
        layer_tt = (layer.get("encoding") or {}).get("tooltip")
        if isinstance(layer_tt, list):
            _patch_tooltip_list(layer_tt)
    return spec


def _fix_truncated_time_axis(spec: dict, query_summary: dict | None) -> dict:
    if not query_summary or not query_summary.get("was_truncated"):
        return spec
    date_col_stat = next(
        (c for c in (query_summary.get("columns") or [])
         if c.get("dtype") == "date" and c.get("min") and c.get("max")),
        None,
    )
    if not date_col_stat:
        return spec
    domain = [str(date_col_stat["min"]), str(date_col_stat["max"])]
    spec = copy.deepcopy(spec)
    def _apply_domain(encoding: dict) -> None:
        for ch in encoding.values():
            if not isinstance(ch, dict):
                continue
            if ch.get("type") == "temporal" and ch.get("field"):
                ch.setdefault("scale", {})["domain"] = domain
    if isinstance(spec.get("encoding"), dict):
        _apply_domain(spec["encoding"])
    for layer in spec.get("layer", []):
        if isinstance(layer, dict) and isinstance(layer.get("encoding"), dict):
            _apply_domain(layer["encoding"])
    return spec


def _postprocess_spec(spec: dict, columns: list[str], rows: list[list], plan: dict, query_summary: dict | None = None) -> dict:
    spec = _humanize_spec_labels(spec)
    spec = _fix_large_number_axes(spec, columns, rows, plan)
    spec = _fix_large_number_tooltips(spec, columns, rows, plan)
    spec = _fix_truncated_time_axis(spec, query_summary)
    return spec


# ─── Safe value format ────────────────────────────────────────────────────────

def _safe_value_format(fmt: str, rows: list[list], col_idx: int) -> str:
    fmt = fmt.replace("~", "")
    if "%" not in fmt:
        return fmt
    try:
        sample = [float(rows[j][col_idx]) for j in range(min(10, len(rows))) if rows[j][col_idx] is not None]
        if sample:
            mx = max(abs(v) for v in sample)
            if mx > 100:
                return ",.0f"
            if mx > 1:
                return ",.1f"
    except (TypeError, ValueError, IndexError):
        pass
    return fmt


# ─── Row aggregation ──────────────────────────────────────────────────────────

def _aggregate_rows(
    rows: list[list],
    columns: list[str],
    group_cols: list[str],
    agg_col: str,
    agg_fn: str,
) -> list[list]:
    if not rows or agg_fn == "none":
        return rows
    valid_groups = [c for c in group_cols if c and c in columns]
    if not valid_groups or agg_col not in columns:
        return rows
    gi = [columns.index(c) for c in valid_groups]
    ai = columns.index(agg_col)
    group_keys = [tuple(r[i] if i < len(r) else None for i in gi) for r in rows]
    if len(set(group_keys)) == len(rows):
        return rows
    agg_map: dict = {}
    for r, key in zip(rows, group_keys):
        if key not in agg_map:
            agg_map[key] = [list(r), []]
        try:
            val = float(r[ai]) if ai < len(r) and r[ai] is not None else None
        except (TypeError, ValueError):
            val = None
        if val is not None:
            agg_map[key][1].append(val)
    result = []
    for proto, vals in agg_map.values():
        if vals:
            if agg_fn == "avg":
                proto[ai] = sum(vals) / len(vals)
            elif agg_fn == "max":
                proto[ai] = max(vals)
            elif agg_fn == "min":
                proto[ai] = min(vals)
            elif agg_fn == "count":
                proto[ai] = float(len(vals))
            else:
                proto[ai] = sum(vals)
        else:
            proto[ai] = None
        result.append(proto)
    return result


# ─── Row sorting ─────────────────────────────────────────────────────────────

def _sort_rows(rows: list[list], columns: list[str], plan: dict) -> list[list]:
    """Sort rows Python-side per LLM decision. Vega then uses sort=null to preserve order."""
    sort_by = (plan.get("sort_by") or "none").lower()
    sort_order = (plan.get("sort_order") or "ascending").lower()
    reverse = sort_order == "descending"

    if sort_by == "none" or not rows:
        return rows

    if sort_by == "x_column":
        col = plan.get("x_column")
    elif sort_by == "y_column":
        col = plan.get("y_column")
    else:
        col = sort_by  # direct column name

    if not col or col not in columns:
        return rows

    idx = columns.index(col)

    def _key(row):
        v = row[idx] if idx < len(row) else None
        if v is None:
            return (1, 0.0, "")  # nulls last regardless of direction
        try:
            return (0, float(v), "")  # numeric compare
        except (TypeError, ValueError):
            return (0, 0.0, str(v))  # string compare

    try:
        return sorted(rows, key=_key, reverse=reverse)
    except Exception:
        return rows


# ─── LLM call ────────────────────────────────────────────────────────────────

async def _call_chart_llm(
    columns: list[str],
    rows: list[list],
    query_summary: dict,
    state: AnalyticsState,
    config: RunnableConfig,
    persona: str,
) -> dict:
    data_profile = _build_data_profile(columns, rows, query_summary)
    col_meta_str = _build_column_metadata(columns, state)

    fb = state.get("feedback_context") or ""
    feedback_section = (
        f"USER PREFERENCES (apply — these override all defaults):\n<feedback_context>{fb}</feedback_context>"
        if fb else ""
    )

    raw_intent = state.get("query_intent") or []
    if isinstance(raw_intent, list) and raw_intent:
        query_intent_str = "\n".join(f"  {line}" for line in raw_intent)
    else:
        ir_intent = ((state.get("semantic_ir_list") or [{}])[0]).get("intent", "")
        query_intent_str = f"  {ir_intent}" if ir_intent else "  (not available)"

    prompt = CHART_AGENT_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        persona=persona,
        query_intent=query_intent_str,
        data_profile=data_profile,
        column_metadata=col_meta_str,
        feedback_section=feedback_section,
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    _mission = build_mission_context(
        state,
        role="Build a complete chart specification from data",
        feeds="Vega-Lite renderer",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(
            lambda: llm.ainvoke(prompt, config=config),
            service="bedrock-chart-agent",
            max_attempts=2,
            backoff_base=5.0,
        )

    fallback = {
        "chart_type": "bar",
        "chart_confidence": 0,
        "alternative_types": [],
    }

    try:
        response = await _call()
        raw = response.content or ""
        output = parse_tag(raw, "chart")
        if output:
            from json_repair import loads as json_loads
            parsed = json_loads(output)
            if isinstance(parsed, dict) and parsed.get("chart_type") in _VALID_CHART_TYPES:
                for key in ("x_value_format", "y_value_format"):
                    if isinstance(parsed.get(key), str):
                        parsed[key] = parsed[key].replace("~", "")
                logger.info(
                    "chart_agent | LLM plan: type={} x={} x_type={} y={} sort_by={} sort_order={} confidence={}",
                    parsed.get("chart_type"), parsed.get("x_column"), parsed.get("x_column_type"),
                    parsed.get("y_column"), parsed.get("sort_by"), parsed.get("sort_order"),
                    parsed.get("chart_confidence"),
                )
                return parsed
    except Exception as e:
        logger.warning("chart_agent | LLM call failed | error={}", e)

    return fallback


# ─── Vega-Lite spec builder ───────────────────────────────────────────────────

def _build_vega_lite_spec(
    chart_type: str,
    columns: list[str],
    rows: list[list],
    plan: dict,
) -> dict:
    """Build a Vega-Lite spec from LLM-decided plan. No column type inference — uses plan values directly."""
    x_col   = plan.get("x_column") or (columns[0] if columns else "x")
    y_col   = plan.get("y_column") or (columns[-1] if columns else "y")
    c_col   = plan.get("color_column") or None
    sz_col  = plan.get("size_column") or None
    x_type  = plan.get("x_column_type") or "nominal"
    y_fmt   = plan.get("y_value_format") or ",.0f"
    x_fmt   = plan.get("x_value_format")
    x_title = plan.get("x_axis_label") or _snake_to_title(x_col)
    y_title = plan.get("y_axis_label") or _snake_to_title(y_col)
    title   = plan.get("chart_title") or ""
    leg_ttl = plan.get("legend_title") or ""
    agg_fn  = (plan.get("agg_function") or "none").lower()

    # Validate column names
    if x_col not in columns:
        x_col = columns[0] if columns else x_col
    if y_col not in columns:
        y_col = next((c for c in reversed(columns) if c != x_col), columns[-1] if len(columns) > 1 else x_col)
    if c_col and c_col not in columns:
        c_col = None
    if sz_col and sz_col not in columns:
        sz_col = None

    y_idx = columns.index(y_col) if y_col in columns else -1
    safe_y_fmt = _safe_value_format(y_fmt, rows, y_idx)

    base = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "width": "container",
        "height": 350,
        "background": "transparent",
        "title": title,
        "data": {"values": [dict(zip(columns, row)) for row in rows[:500]]},
        "config": _BASE_CONFIG,
    }

    # ── KPI card ──────────────────────────────────────────────────────────────
    if chart_type == "kpi_card":
        base["type"] = "kpi_card"
        base["values"] = [dict(zip(columns, row)) for row in rows[:3]]
        base["value_format"] = safe_y_fmt
        return base

    # ── Line ──────────────────────────────────────────────────────────────────
    if chart_type == "line":
        data_rows = _aggregate_rows(rows, columns, [c for c in [x_col, c_col] if c], y_col, agg_fn)
        if x_type == "temporal":
            x_enc = {"field": x_col, "type": "temporal", "timeUnit": "yearmonthdate",
                     "axis": {"title": x_title, "format": "%b %d, %Y", "labelAngle": -30}}
            tt_x  = {"field": x_col, "type": "temporal", "timeUnit": "yearmonthdate",
                     "format": "%b %d, %Y", "title": x_title}
        else:
            x_enc = {"field": x_col, "type": x_type, "sort": None,
                     "axis": {"title": x_title, "labelAngle": -30}}
            tt_x  = {"field": x_col, "type": x_type, "title": x_title}
        enc = {
            "x": x_enc,
            "y": {"field": y_col, "type": "quantitative",
                  "axis": {"title": y_title, "format": safe_y_fmt}},
            "tooltip": [tt_x, {"field": y_col, "type": "quantitative",
                                "format": safe_y_fmt, "title": y_title}],
        }
        if c_col:
            enc["color"] = {"field": c_col, "type": "nominal",
                             **({"legend": {"title": leg_ttl}} if leg_ttl else {})}
            enc["tooltip"].append({"field": c_col, "type": "nominal"})
        return {**base, "data": {"values": [dict(zip(columns, r)) for r in data_rows]},
                "mark": {"type": "line", "point": True}, "encoding": enc}

    # ── Bar ───────────────────────────────────────────────────────────────────
    if chart_type == "bar":
        data_rows = _aggregate_rows(rows, columns, [c for c in [x_col, c_col] if c], y_col, agg_fn)
        data_rows = data_rows[:_MAX_BAR_CATS]
        bar_title = title
        if len(data_rows) == _MAX_BAR_CATS and len(rows) > _MAX_BAR_CATS:
            bar_title = f"{title} (top {_MAX_BAR_CATS})" if title else f"Top {_MAX_BAR_CATS}"
        y_idx2 = columns.index(y_col) if y_col in columns else -1
        safe_fmt = _safe_value_format(y_fmt, data_rows, y_idx2)
        has_neg = any(
            y_idx2 < len(r) and r[y_idx2] is not None and float(r[y_idx2]) < 0
            for r in data_rows[:50]
        ) if y_idx2 >= 0 else False
        color_enc: dict = (
            {"condition": {"test": f"datum['{y_col}'] < 0", "value": "#e45755"}, "value": "#4c78a8"}
            if has_neg else {}
        )
        enc = {
            "x": {"field": x_col, "type": x_type, "sort": None,
                  "axis": {"title": x_title, "labelAngle": -45, "labelLimit": 100}},
            "y": {"field": y_col, "type": "quantitative",
                  "axis": {"title": y_title, "format": safe_fmt}},
            "tooltip": [
                {"field": x_col, "type": x_type},
                {"field": y_col, "type": "quantitative", "format": safe_fmt, "title": y_title},
            ],
        }
        if color_enc:
            enc["color"] = color_enc
        elif c_col and c_col in columns:
            n_unique = len({str(r[columns.index(c_col)]) for r in data_rows if columns.index(c_col) < len(r)})
            if 1 < n_unique <= 20:
                enc["color"] = {"field": c_col, "type": "nominal",
                                 **({"legend": {"title": leg_ttl}} if leg_ttl else {})}
                enc["tooltip"].append({"field": c_col, "type": "nominal"})
        return {**base, "title": bar_title,
                "data": {"values": [dict(zip(columns, r)) for r in data_rows]},
                "mark": "bar", "encoding": enc}

    # ── Grouped bar ───────────────────────────────────────────────────────────
    if chart_type == "grouped_bar":
        group_col = c_col or (next((c for c in columns if c != x_col and c != y_col), None))
        data_rows = _aggregate_rows(rows, columns, [c for c in [x_col, group_col] if c], y_col, agg_fn)
        y_idx2 = columns.index(y_col) if y_col in columns else -1
        safe_fmt = _safe_value_format(y_fmt, data_rows, y_idx2)
        enc = {
            "x": {"field": x_col, "type": x_type, "sort": None,
                  "axis": {"title": x_title, "labelAngle": -30}},
            "y": {"field": y_col, "type": "quantitative",
                  "axis": {"title": y_title, "format": safe_fmt}},
            "tooltip": [
                {"field": x_col, "type": x_type},
                {"field": y_col, "type": "quantitative", "format": safe_fmt},
            ],
        }
        if group_col and group_col in columns:
            enc["color"] = {"field": group_col, "type": "nominal",
                             **({"legend": {"title": leg_ttl}} if leg_ttl else {})}
            enc["xOffset"] = {"field": group_col}
            enc["tooltip"].append({"field": group_col, "type": "nominal"})
        return {**base, "data": {"values": [dict(zip(columns, r)) for r in data_rows]},
                "mark": "bar", "encoding": enc}

    # ── Donut ─────────────────────────────────────────────────────────────────
    if chart_type in ("donut", "pie"):
        data_rows = _aggregate_rows(rows, columns, [x_col], y_col, agg_fn)

        y_idx_d = columns.index(y_col) if y_col in columns else -1
        total_val = sum(
            abs(float(r[y_idx_d])) for r in data_rows
            if y_idx_d >= 0 and r[y_idx_d] is not None
        ) if y_idx_d >= 0 else 0.0
        has_tiny = total_val > 0 and any(
            (abs(float(r[y_idx_d])) / total_val) < 0.05
            for r in data_rows if y_idx_d >= 0 and r[y_idx_d] is not None
        )

        data_values: list[dict] = []
        for r in data_rows:
            row_dict = dict(zip(columns, r))
            if has_tiny and total_val > 0 and y_idx_d >= 0 and r[y_idx_d] is not None:
                pct = abs(float(r[y_idx_d])) / total_val * 100
                row_dict["_pct_label"] = f"{pct:.1f}%"
            data_values.append(row_dict)

        theta_enc: dict = {"field": y_col, "type": "quantitative", "stack": True}
        color_enc: dict = {
            "field": x_col, "type": "nominal",
            "legend": {"title": leg_ttl or _snake_to_title(x_col)},
        }
        arc_tooltip = [
            {"field": x_col, "type": "nominal"},
            {"field": y_col, "type": "quantitative", "format": safe_y_fmt, "title": y_title},
        ]
        if has_tiny:
            arc_tooltip.append({"field": "_pct_label", "type": "nominal", "title": "Share"})

        arc_layer: dict = {
            "mark": {"type": "arc", "innerRadius": 65, "padAngle": 0.015, "cornerRadius": 3},
            "encoding": {"theta": theta_enc, "color": color_enc, "tooltip": arc_tooltip},
        }

        if has_tiny:
            return {
                **base,
                "data": {"values": data_values},
                "layer": [
                    arc_layer,
                    {
                        "mark": {"type": "text", "radius": 148, "fontSize": 10, "color": "#555"},
                        "encoding": {
                            "theta": theta_enc,
                            "text": {"field": "_pct_label", "type": "nominal"},
                        },
                    },
                ],
            }
        return {
            **base,
            "data": {"values": data_values},
            **arc_layer,
        }

    # ── Scatter ───────────────────────────────────────────────────────────────
    if chart_type == "scatter":
        x_idx = columns.index(x_col) if x_col in columns else -1
        safe_x_fmt = _safe_value_format(x_fmt or ",.0f", rows, x_idx)
        enc = {
            "x": {"field": x_col, "type": "quantitative",
                  "axis": {"title": x_title, "format": safe_x_fmt}},
            "y": {"field": y_col, "type": "quantitative",
                  "axis": {"title": y_title, "format": safe_y_fmt}},
            "tooltip": [
                {"field": x_col, "type": "quantitative", "format": safe_x_fmt},
                {"field": y_col, "type": "quantitative", "format": safe_y_fmt},
            ],
        }
        if c_col:
            enc["color"] = {"field": c_col, "type": "nominal"}
            enc["tooltip"].append({"field": c_col, "type": "nominal"})
        return {**base, "mark": {"type": "point", "filled": True}, "encoding": enc}

    # ── Heatmap ───────────────────────────────────────────────────────────────
    if chart_type == "heatmap":
        y2_col = c_col or next((c for c in columns if c != x_col and c != y_col), y_col)
        data_rows = _aggregate_rows(rows, columns, [x_col, y2_col], y_col, agg_fn)
        return {
            **base,
            "data": {"values": [dict(zip(columns, r)) for r in data_rows]},
            "mark": {"type": "rect"},
            "encoding": {
                "x":     {"field": x_col,  "type": x_type,         "sort": None, "axis": {"title": x_title}},
                "y":     {"field": y2_col, "type": "nominal",       "axis": {"title": leg_ttl or _snake_to_title(y2_col)}},
                "color": {"field": y_col,  "type": "quantitative",  "scale": {"scheme": "blues"},
                          "legend": {"title": y_title, "format": safe_y_fmt}},
                "tooltip": [
                    {"field": x_col,  "type": x_type},
                    {"field": y2_col, "type": "nominal"},
                    {"field": y_col,  "type": "quantitative", "format": safe_y_fmt},
                ],
            },
        }

    # ── Waterfall ─────────────────────────────────────────────────────────────
    if chart_type == "waterfall":
        if x_col in columns and y_col in columns:
            xi = columns.index(x_col)
            yi = columns.index(y_col)
            agg: dict = {}
            for r in rows:
                xv = str(r[xi]) if xi < len(r) else ""
                try:
                    yv = float(r[yi]) if yi < len(r) and r[yi] is not None else 0.0
                except (TypeError, ValueError):
                    yv = 0.0
                agg[xv] = agg.get(xv, 0.0) + yv
            wf_rows = [[k, v] for k, v in agg.items()][:_WATERFALL_MAX_ROWS]
            wf_columns = [x_col, y_col]
        else:
            wf_rows = rows[:_WATERFALL_MAX_ROWS]
            wf_columns = columns

        y_idx2 = 1
        safe_fmt = _safe_value_format(y_fmt, wf_rows, y_idx2)
        spec = {
            **base,
            "data": {"values": [dict(zip(wf_columns, r)) for r in wf_rows]},
            "mark": {"type": "bar", "cornerRadiusTopLeft": 2, "cornerRadiusTopRight": 2},
            "transform": [
                {"window": [{"op": "sum", "field": y_col, "as": "_wf_sum"}], "frame": [None, 0]},
                {"calculate": f"datum._wf_sum - datum['{y_col}']", "as": "_wf_prev"},
            ],
            "encoding": {
                "x": {"field": x_col, "type": "nominal", "sort": None,
                      "axis": {"title": x_title, "labelAngle": -30, "labelLimit": 120}},
                "y":  {"field": "_wf_sum",  "type": "quantitative",
                       "axis": {"title": y_title, "format": safe_fmt}},
                "y2": {"field": "_wf_prev"},
                "color": {
                    "condition": {"test": f"datum['{y_col}'] < 0", "value": "#e45755"},
                    "value": "#4c78a8",
                },
                "tooltip": [
                    {"field": x_col,     "type": "nominal"},
                    {"field": y_col,     "type": "quantitative", "format": safe_fmt, "title": "Change"},
                    {"field": "_wf_sum", "type": "quantitative", "format": safe_fmt, "title": "Running Total"},
                ],
            },
        }
        try:
            _running, _wf_cum_max = 0.0, 0.0
            for _r in wf_rows:
                try:
                    _running += float(_r[1]) if _r[1] is not None else 0.0
                    _wf_cum_max = max(_wf_cum_max, abs(_running))
                except (TypeError, ValueError):
                    pass
            if _wf_cum_max >= 1_000_000:
                _wf_csym = _extract_currency_symbol(safe_fmt)
                _wf_ax = spec["encoding"]["y"].get("axis")
                if not isinstance(_wf_ax, dict):
                    _wf_ax = {}
                    spec["encoding"]["y"]["axis"] = _wf_ax
                _wf_ax.pop("format", None)
                _wf_ax["labelExpr"] = _build_label_expr(_wf_csym)
        except Exception:
            pass
        return spec

    # ── Fallback ──────────────────────────────────────────────────────────────
    return {
        **base,
        "mark": "bar",
        "encoding": {
            "x": {"field": x_col, "type": "nominal", "sort": None},
            "y": {"field": y_col, "type": "quantitative", "axis": {"format": safe_y_fmt}},
        },
    }


# ─── Spec validation ─────────────────────────────────────────────────────────

def _validate_spec(spec: dict, chart_type: str) -> bool:
    if not spec:
        return False
    if spec.get("type") == "kpi_card":
        return bool(spec.get("values"))
    if not spec.get("$schema"):
        return False
    if not spec.get("mark"):
        return False
    enc = spec.get("encoding") or {}
    if chart_type == "line":
        x_type = (enc.get("x") or {}).get("type")
        if x_type not in ("temporal", "ordinal", "nominal"):
            return False
    if chart_type in ("pie", "donut"):
        return bool(enc.get("theta"))
    if chart_type not in ("pie", "donut", "kpi_card", "heatmap", "waterfall"):
        if not enc.get("x") or not enc.get("y"):
            return False
    return True


# ─── Alternative specs ────────────────────────────────────────────────────────

def _build_alternative_specs(
    plan: dict,
    primary_type: str,
    columns: list[str],
    rows: list[list],
    query_summary: dict | None = None,
) -> list[dict]:
    seen = {primary_type, "table"}
    result = []
    for alt in (plan.get("alternative_types") or [])[:3]:
        if not isinstance(alt, dict):
            continue
        alt_type = alt.get("type", "")
        if not alt_type or alt_type in seen or alt_type not in _VALID_CHART_TYPES:
            continue
        if int(alt.get("confidence", 100)) < 60:
            continue
        seen.add(alt_type)
        try:
            alt_plan = {**plan, **alt, "chart_type": alt_type}
            alt_rows = _sort_rows(rows, columns, alt_plan)
            spec = _postprocess_spec(
                _build_vega_lite_spec(alt_type, columns, alt_rows, alt_plan),
                columns, alt_rows, alt_plan,
                query_summary=query_summary,
            )
            if _validate_spec(spec, alt_type):
                result.append({"chart_type": alt_type, "spec": spec})
        except Exception as e:
            logger.debug("chart_agent | alt spec failed | type={} | error={}", alt_type, e)
    return result


# ─── Main node ────────────────────────────────────────────────────────────────

async def chart_agent(state: AnalyticsState, config: RunnableConfig) -> dict:
    query_summary = state.get("query_summary") or {}
    result_list   = state.get("result_list") or []

    all_columns: list[str] = (
        [c["name"] for c in query_summary["columns"]]
        if query_summary.get("columns") else []
    )
    all_rows: list[list] = []
    for res in result_list:
        if res.get("rows"):
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            all_rows.extend(res["rows"])

    if not all_columns or not all_rows:
        logger.warning("chart_agent | no data | thread={}", state["thread_id"])
        return {"chart_spec": None}

    all_rows = _drop_blank_rows(all_columns, all_rows)
    if not all_rows:
        return {"chart_spec": None}
    all_columns, all_rows = _drop_blank_columns(all_columns, all_rows)
    if not all_columns:
        return {"chart_spec": None}

    persona = state.get("persona", "analyst")

    # Short-circuit: 1-row result → kpi_card directly, no LLM call needed
    if len(all_rows) == 1:
        _y_col = all_columns[-1]
        for _ci, _cn in enumerate(all_columns):
            try:
                float(all_rows[0][_ci])
                _y_col = _cn
                break
            except (TypeError, ValueError):
                continue
        _y_val = all_rows[0][all_columns.index(_y_col)] if _y_col in all_columns else None
        _y_fmt = ",.0f"
        try:
            _fv = float(_y_val)
            if _fv != int(_fv):
                _y_fmt = ",.2f"
        except (TypeError, ValueError):
            pass
        _kpi_plan = {"chart_type": "kpi_card", "y_column": _y_col, "y_value_format": _y_fmt,
                     "chart_confidence": 100, "alternative_types": []}
        _kpi_spec = _build_vega_lite_spec("kpi_card", all_columns, all_rows, _kpi_plan)
        if _validate_spec(_kpi_spec, "kpi_card"):
            logger.info("chart_agent | 1-row → kpi_card (no LLM) | thread={}", state["thread_id"])
            return {"chart_spec": _kpi_spec, "chart_type": "kpi_card", "alternative_chart_specs": []}

    plan = await _call_chart_llm(all_columns, all_rows, query_summary, state, config, persona)

    chart_type = plan.get("chart_type", "bar")
    confidence = int(plan.get("chart_confidence", 100))

    if chart_type == "table" or confidence < 60:
        logger.info("chart_agent | low confidence ({}) or table → no chart | thread={}", confidence, state["thread_id"])
        return {"chart_spec": None, "chart_type": "table", "alternative_chart_specs": []}

    sorted_rows = _sort_rows(all_rows, all_columns, plan)

    spec = _postprocess_spec(
        _build_vega_lite_spec(chart_type, all_columns, sorted_rows, plan),
        all_columns, sorted_rows, plan,
        query_summary=query_summary,
    )

    if not _validate_spec(spec, chart_type):
        logger.info("chart_agent | spec validation failed | type={} | thread={}", chart_type, state["thread_id"])
        return {"chart_spec": None, "chart_type": None, "alternative_chart_specs": []}

    alternative_specs = _build_alternative_specs(plan, chart_type, all_columns, all_rows, query_summary)

    logger.info(
        "chart_agent DONE | thread={} | type={} | alternatives={}",
        state["thread_id"], chart_type, [a["chart_type"] for a in alternative_specs],
    )
    return {"chart_spec": spec, "chart_type": chart_type, "alternative_chart_specs": alternative_specs}


def _build_table_spec(columns: list[str], rows: list[list]) -> dict:
    return {
        "type": "table",
        "columns": columns,
        "rows": [list(r) for r in rows[:200]],
    }

"""Streaming helpers and data utilities for the MTI Brain pipeline.

Mirrors quest/backend_graph streaming infrastructure exactly, extended with
SPARQL-specific parsing and chart data utilities.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any


# ─── Tag parsing ──────────────────────────────────────────────────────────────

def parse_tag(text: str, tag: str) -> str:
    """Extract content from an XML-style tag in LLM output.

    For sparql tags, strict extraction is used — never falls back to raw text,
    because leaked reasoning would corrupt the query. For all other tags,
    falls back to stripping XML wrappers.
    """
    m = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    if tag.lower() == "sparql":
        m = re.search(r"```(?:sparql)?\s*(.*?)```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            first = candidate.upper().split()[0] if candidate.split() else ""
            if first in ("SELECT", "ASK", "PREFIX"):
                return candidate
        m = re.search(r"<sparql>(.*)", text, re.DOTALL | re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            first = candidate.upper().split()[0] if candidate.split() else ""
            if first in ("SELECT", "ASK", "PREFIX"):
                return candidate
        return ""

    stripped = re.sub(r"</?[a-zA-Z_]+>", "", text).strip()
    if stripped and not stripped.startswith("["):
        return stripped
    return ""


def _format_recent_messages(messages: list, n: int = 6, max_chars: int = 400) -> str:
    """Format the last N messages as readable conversation history for LLM context."""
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-n:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:max_chars]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _format_scratchpad_context(scratchpad: dict, dep_ids: list[str]) -> str:
    """Format completed dependency results as context for a sub-question."""
    if not dep_ids or not scratchpad:
        return ""
    parts = []
    for dep_id in dep_ids:
        result = scratchpad.get(dep_id)
        if not result:
            continue
        answer = result.get("answer", "")
        cols = result.get("kg_columns", [])
        rows = result.get("kg_rows", [])[:5]
        summary = answer[:300] if answer else ""
        if cols and rows:
            header = " | ".join(cols)
            data = "\n".join(" | ".join(str(v) for v in r) for r in rows)
            summary = f"{header}\n{data}"
        parts.append(f"[Dependency {dep_id}]: {summary}")
    return "\n\n".join(parts)


def parse_sparql_from_response(text: str) -> str:
    """Extract a SPARQL SELECT/ASK query from LLM output."""
    return parse_tag(text, "sparql")


def parse_json_from_response(text: str) -> dict:
    """Extract and parse a JSON object from LLM output."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except (json.JSONDecodeError, ValueError):
        return {}


# ─── Section streamers (mirror quest exactly) ─────────────────────────────────

class MultiSectionStreamer:
    """Stream content from multiple XML sections in sequence.

    Each section has its own tag and SSE event type. As tokens arrive,
    the streamer detects which section is active and emits content with
    the corresponding event type.
    """

    def __init__(self, sections: list[tuple[str, str]]) -> None:
        self.sections = [
            {
                "open": f"<{tag.lower()}>",
                "close": f"</{tag.lower()}>",
                "etype": etype,
                "done": False,
            }
            for tag, etype in sections
        ]
        self.active_idx: int | None = None
        self.buf = ""
        self.done = False

    def reset(self) -> None:
        for s in self.sections:
            s["done"] = False
        self.active_idx = None
        self.buf = ""
        self.done = False

    def feed(self, token: str) -> tuple[str, str]:
        """Feed a token and return (emittable_text, sse_event_type).

        Returns ("", "") if nothing to emit yet.
        """
        if self.done:
            return "", ""
        self.buf += token
        buf_lower = self.buf.lower()

        if self.active_idx is None:
            for i, s in enumerate(self.sections):
                if s["done"]:
                    continue
                if s["open"] in buf_lower:
                    self.active_idx = i
                    idx = buf_lower.index(s["open"]) + len(s["open"])
                    self.buf = self.buf[idx:]
                    break
            else:
                max_tag_len = max(len(s["open"]) for s in self.sections)
                if len(self.buf) > max_tag_len:
                    self.buf = self.buf[-(max_tag_len - 1):]
                return "", ""

        s = self.sections[self.active_idx]
        buf_lower = self.buf.lower()
        if s["close"] in buf_lower:
            idx = buf_lower.index(s["close"])
            emit = self.buf[:idx]
            self.buf = self.buf[idx + len(s["close"]):]
            s["done"] = True
            etype = s["etype"]
            self.active_idx = None
            if all(sec["done"] for sec in self.sections):
                self.done = True
            return emit, etype

        hold = len(s["close"]) - 1
        if len(self.buf) > hold:
            emit, self.buf = self.buf[:-hold], self.buf[-hold:]
            return emit, s["etype"]
        return "", ""


class SectionStreamer:
    """Stream text content from XML sections token by token.

    Handles every occurrence of the tag within a node invocation so that
    retry blocks all reach the UI, not just the first.
    """

    def __init__(self, tag: str) -> None:
        self.open_tag = f"<{tag.lower()}>"
        self.close_tag = f"</{tag.lower()}>"
        self.active = False
        self.buf = ""

    def reset(self) -> None:
        self.active = False
        self.buf = ""

    def feed(self, token: str) -> str:
        """Feed a token and return emittable text (empty string when buffering)."""
        self.buf += token
        emitted: list[str] = []
        while True:
            buf_lower = self.buf.lower()
            if not self.active:
                if self.open_tag in buf_lower:
                    self.active = True
                    idx = buf_lower.index(self.open_tag) + len(self.open_tag)
                    self.buf = self.buf[idx:]
                    continue
                hold = len(self.open_tag) - 1
                if len(self.buf) > hold:
                    self.buf = self.buf[-hold:]
                break
            buf_lower = self.buf.lower()
            if self.close_tag in buf_lower:
                idx = buf_lower.index(self.close_tag)
                if idx:
                    emitted.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.close_tag):]
                self.active = False
                continue
            hold = len(self.close_tag) - 1
            if len(self.buf) > hold:
                emitted.append(self.buf[:-hold])
                self.buf = self.buf[-hold:]
            break
        return "".join(emitted)


# ─── Chart data utilities ─────────────────────────────────────────────────────

_NARRATIVE_SAMPLE_CAP = 100   # kept for callers that reference it directly
_SAMPLE_PCT           = 0.20  # 20 % of valid rows to include in the spread
_SAMPLE_MIN           = 20    # always at least this many rows in the spread


def _spread_sample(rows: list[list], pct: float = _SAMPLE_PCT,
                   min_rows: int = _SAMPLE_MIN,
                   max_rows: int = _NARRATIVE_SAMPLE_CAP) -> list[list]:
    """Return a representative spread sample from a row set.

    Algorithm:
      1. Remove rows that are entirely None (completely empty — add no signal).
      2. Compute target = max(min_rows, min(max_rows, int(n_valid * pct))).
      3. If n_valid ≤ target, return all valid rows.
      4. Otherwise distribute target slots across four bands:
           head (30 %)  · Q1/25 % mark (20 %)  · Q3/75 % mark (20 %)  · tail (30 %)
         Each band takes a contiguous slice centred on its position so that
         rows are never reordered and neighbouring context is preserved.
      5. Deduplicate while preserving order (bands can overlap for small n).

    Why: null rows can cluster anywhere; fixed head/tail indices miss valid data
    in the middle.  Percentage bands guarantee coverage across the full
    distribution regardless of dataset size or null pattern.
    """
    if not rows:
        return []

    # Step 1 — strip fully-null rows
    valid = [r for r in rows if any(v is not None for v in r)]
    if not valid:
        return rows[:max_rows]   # fallback: return raw rows, nulls and all

    n = len(valid)
    target = max(min_rows, min(max_rows, int(n * pct)))

    if n <= target:
        return valid

    # Band sizes (must sum to target)
    n_head = max(3, round(target * 0.30))
    n_q1   = max(2, round(target * 0.20))
    n_q3   = max(2, round(target * 0.20))
    n_tail = target - n_head - n_q1 - n_q3

    def _band(center: int, size: int) -> list[list]:
        half = size // 2
        lo = max(0, center - half)
        hi = min(n, lo + size)
        lo = max(0, hi - size)   # re-anchor if we hit the top
        return valid[lo:hi]

    q1_center = n // 4
    q3_center = 3 * n // 4

    # Build combined list, deduplicating by object identity
    seen: set[int] = set()
    result: list[list] = []
    for row in (valid[:n_head]
                + _band(q1_center, n_q1)
                + _band(q3_center, n_q3)
                + valid[-n_tail:]):
        rid = id(row)
        if rid not in seen:
            seen.add(rid)
            result.append(row)

    return result


def _build_data_summary(
    columns: list[str], rows: list[list]
) -> tuple[str, list[str], list[list]]:
    """Build column stats and a spread sample from query results.

    Computed once over ALL rows so both the narrative and chart agents have
    an accurate picture of the full dataset.

    Returns (col_stats, null_notes, spread_sample).
    The spread_sample is generated by _spread_sample() — percentage-based,
    null-filtered, start/Q1/Q3/end coverage.
    """
    import re as _re
    from datetime import date as _date

    if not columns or not rows:
        return "", [], []

    n = len(rows)
    col_idx = {c: i for i, c in enumerate(columns)}

    def _percentile(sorted_vals: list[float], p: float) -> float:
        k = (len(sorted_vals) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

    def _date_span(start: str, end: str) -> str:
        try:
            d0 = _date.fromisoformat(start[:10])
            d1 = _date.fromisoformat(end[:10])
        except Exception:
            return ""
        days = (d1 - d0).days
        if days >= 730:
            return f"{days}d (~{days // 365}y)"
        if days >= 60:
            return f"{days}d (~{days // 30}mo)"
        return f"{days}d"

    stats_parts = []
    null_notes = []

    for c in columns:
        ci = col_idx[c]
        all_vals = [r[ci] for r in rows]
        null_count = sum(1 for v in all_vals if v is None)
        vals = [v for v in all_vals if v is not None]
        if null_count > 0:
            null_notes.append(f"{c}: {null_count}/{n} null")
        if not vals:
            stats_parts.append(f"{c}: all null ({n} rows)")
            continue
        unique = len(set(str(v) for v in vals))
        str_vals = [str(v) for v in vals]
        try:
            nums = [float(v) for v in vals]
            nums_sorted = sorted(nums)
            total = sum(nums)
            avg = total / len(nums)
            median = _percentile(nums_sorted, 0.5)
            p25 = _percentile(nums_sorted, 0.25)
            p75 = _percentile(nums_sorted, 0.75)
            stats_parts.append(
                f"{c}: {unique} unique, min={min(nums)}, max={max(nums)}, "
                f"avg={avg:.2f}, median={median:.2f}, p25/p75={p25:.2f}/{p75:.2f}, "
                f"sum={total:.2f}"
            )
            continue
        except (TypeError, ValueError):
            pass
        if str_vals and _re.match(r"\d{4}-\d{2}-\d{2}", str_vals[0]):
            sorted_v = sorted(str_vals)
            start, end = sorted_v[0], sorted_v[-1]
            parts = [f"{unique} unique", f"range={start} to {end}"]
            span = _date_span(start, end)
            if span:
                parts.append(f"span={span}")
            top_dates = Counter(str_vals).most_common(3)
            top_dates = [(v, cnt) for v, cnt in top_dates if cnt > 1]
            if top_dates:
                top_str = ", ".join(f'"{v}"({cnt})' for v, cnt in top_dates)
                parts.append(f"top: {top_str}")
            stats_parts.append(f"{c}: " + ", ".join(parts) + " [DATE]")
            continue
        top = Counter(str_vals).most_common(5)
        top_str = ", ".join(f'"{v}"({cnt})' for v, cnt in top)
        lens = [len(s) for s in str_vals]
        if lens and max(lens) != min(lens):
            avg_len = sum(lens) / len(lens)
            stats_parts.append(
                f"{c}: {unique} unique, avg_len={avg_len:.0f}, max_len={max(lens)}, "
                f"top: {top_str}"
            )
        else:
            stats_parts.append(f"{c}: {unique} unique, top: {top_str}")

    col_stats = "Column stats: " + " | ".join(stats_parts)

    # Spread sample — percentage-based, null-filtered, start/Q1/Q3/end bands.
    # Source-grouped datasets sample proportionally per source so no single
    # source dominates; all others go through the generic _spread_sample.
    if columns and columns[0] == "source":
        from collections import defaultdict
        by_source: dict = defaultdict(list)
        for r in rows:
            by_source[str(r[0])].append(r)
        # Each source gets a proportional share of the overall cap
        per_source_cap = max(3, _NARRATIVE_SAMPLE_CAP // max(1, len(by_source)))
        spread_sample = []
        for src_rows in by_source.values():
            spread_sample.extend(_spread_sample(src_rows, max_rows=per_source_cap))
    else:
        spread_sample = _spread_sample(rows)

    return col_stats, null_notes, spread_sample


def _build_chart_data(spec: dict, columns: list[str], rows: list[list]) -> dict:
    """Populate a chart spec with data rows and apply guardrails.

    Applies limit, sort, key validation, and type checks. Returns {} if
    the spec is invalid or should be skipped.
    """
    if not spec or not spec.get("type"):
        return {}

    chart_type = spec.get("type", "").lower()
    col_set = set(columns)

    def _col_exists(key: str | None) -> bool:
        return key is not None and key in col_set

    def _col_index(key: str) -> int:
        return columns.index(key)

    def _is_numeric_col(key: str) -> bool:
        idx = _col_index(key)
        sample = [r[idx] for r in rows[:20] if r[idx] is not None]
        if not sample:
            return False
        try:
            [float(v) for v in sample]
            return True
        except (TypeError, ValueError):
            return False

    limit = spec.get("limit")
    data = rows[:limit] if limit else rows

    sort = spec.get("sort")

    if chart_type == "pie":
        name_key = spec.get("name_key")
        value_key = spec.get("value_key")
        if not _col_exists(name_key) or not _col_exists(value_key):
            return {}
        if not _is_numeric_col(value_key):
            return {}
        ni, vi = _col_index(name_key), _col_index(value_key)
        pie_data = [{"name": r[ni], "value": r[vi]} for r in data if r[ni] is not None]
        if sort == "desc":
            pie_data.sort(key=lambda x: (x["value"] or 0), reverse=True)
        elif sort == "asc":
            pie_data.sort(key=lambda x: (x["value"] or 0))
        return {**spec, "data": pie_data}

    if chart_type in ("bar", "line", "area"):
        x_key = spec.get("x_key")
        y_keys = spec.get("y_keys", [])
        if not _col_exists(x_key) or not y_keys:
            return {}
        y_keys = [k for k in y_keys if _col_exists(k) and _is_numeric_col(k)]
        if not y_keys:
            return {}
        xi = _col_index(x_key)
        yis = [_col_index(k) for k in y_keys]
        chart_data = []
        for r in data:
            point: dict[str, Any] = {x_key: r[xi]}
            for k, yi in zip(y_keys, yis):
                point[k] = r[yi]
            chart_data.append(point)
        if sort == "desc" and y_keys:
            yi0 = 0
            chart_data.sort(key=lambda p: (p.get(y_keys[yi0]) or 0), reverse=True)
        elif sort == "asc" and y_keys:
            yi0 = 0
            chart_data.sort(key=lambda p: (p.get(y_keys[yi0]) or 0))
        return {**spec, "y_keys": y_keys, "data": chart_data}

    if chart_type == "scatter":
        x_key = spec.get("x_key")
        y_key = spec.get("y_key")
        if not _col_exists(x_key) or not _col_exists(y_key):
            return {}
        if not _is_numeric_col(x_key) or not _is_numeric_col(y_key):
            return {}
        xi, yi = _col_index(x_key), _col_index(y_key)
        scatter_data = [{"x": r[xi], "y": r[yi]} for r in data if r[xi] is not None and r[yi] is not None]
        return {**spec, "data": scatter_data}

    return {}

"""Streaming helpers for the MTI Brain analytics pipeline."""

from __future__ import annotations

import re
from collections import Counter


# Maximum rows passed to the narrative LLM in synthesis nodes.
_NARRATIVE_SAMPLE_CAP = 30


def _build_data_summary(
    columns: list[str],
    rows: list[list],
) -> tuple[str, str, list[list]]:
    """Compute per-column stats and a representative row sample for LLM prompts.

    Returns:
        col_stats    — one line per column: type, range/top-values, null rate
        null_notes   — human-readable note for columns with >20% nulls (empty string if none)
        sampled_rows — head + middle + tail sample capped at 20 rows, as raw lists
    """
    if not columns or not rows:
        return "", "", rows or []

    total = len(rows)
    stat_lines: list[str] = []
    null_note_lines: list[str] = []

    for i, col in enumerate(columns):
        all_vals = [r[i] if i < len(r) else None for r in rows]
        null_count = sum(1 for v in all_vals if v is None)
        vals = [v for v in all_vals if v is not None]

        if null_count and null_count / total > 0.2:
            null_note_lines.append(f"{col}: {null_count}/{total} nulls ({null_count * 100 // total}%)")

        if not vals:
            stat_lines.append(f"{col} [unknown]: all null")
            continue

        try:
            [float(v) for v in vals[:20]]
            dtype = "numeric"
        except (TypeError, ValueError):
            s0 = str(vals[0])
            dtype = "date" if re.match(r"\d{4}-\d{2}-\d{2}", s0) else "string"

        null_suffix = f" | null={null_count}/{total}" if null_count else ""

        if dtype == "numeric":
            nums = [float(v) for v in vals]
            stat_lines.append(
                f"{col} [numeric]: min={min(nums):g} max={max(nums):g} mean={sum(nums)/len(nums):.4g}{null_suffix}"
            )
        elif dtype == "date":
            sorted_dates = sorted(str(v) for v in vals)
            stat_lines.append(f"{col} [date]: {sorted_dates[0]} → {sorted_dates[-1]}{null_suffix}")
        else:
            str_vals = [str(v) for v in vals]
            top = Counter(str_vals).most_common(5)
            top_str = ", ".join(f'"{v}"({c})' for v, c in top)
            stat_lines.append(
                f"{col} [string]: top=[{top_str}] | distinct={len(set(str_vals))}{null_suffix}"
            )

    return "\n".join(stat_lines), "; ".join(null_note_lines), _spread_sample(rows, cap=20)


def _spread_sample(rows: list[list], cap: int = 50) -> list[list]:
    """Return a representative spread sample from start, Q1, Q3, and end bands.

    Rows with more than half their values null are filtered out first.
    Used by dashboard_prompt, _build_data_summary, and synthesis nodes so that
    all LLM prompts see the same sampling strategy.
    """
    if not rows:
        return []

    n_cols = max(len(r) for r in rows) if rows else 1
    filtered = [r for r in rows if sum(1 for v in r if v is None) <= n_cols // 2] or rows

    n = len(filtered)
    if n <= cap:
        return filtered

    band = max(1, cap // 4)

    start = filtered[:band]
    q1_idx = max(band, int(n * 0.25) - band // 2)
    q1 = filtered[q1_idx:q1_idx + band]
    q3_idx = max(q1_idx + band, int(n * 0.75) - band // 2)
    q3 = filtered[q3_idx:q3_idx + band]
    end = filtered[max(q3_idx + band, n - band):]

    seen: set[int] = set()
    sampled: list[list] = []
    for r in start + q1 + q3 + end:
        if id(r) not in seen:
            seen.add(id(r))
            sampled.append(r)

    return sampled[:cap]


# ─── Tag parsing ──────────────────────────────────────────────────────────────

def parse_tag(text: str, tag: str) -> str:
    """Extract content from an XML-style tag in LLM output.

    For sql tags, strict extraction is used — never falls back to raw text,
    because leaked reasoning would corrupt the query. For all other tags,
    falls back to stripping XML wrappers.
    """
    m = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    if tag.lower() == "sql":
        m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            first = candidate.upper().split()[0] if candidate.split() else ""
            if first in ("SELECT", "WITH"):
                return candidate
        return ""

    stripped = re.sub(r"</?[a-zA-Z_]+>", "", text).strip()
    if stripped and not stripped.startswith("["):
        return stripped
    return ""


def parse_json_from_response(text: str) -> dict:
    """Extract and parse a JSON object from LLM output."""
    from json_repair import loads as json_loads
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        result = json_loads(m.group())
        return result if isinstance(result, dict) else {}
    except Exception:
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


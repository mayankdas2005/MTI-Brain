"""Streaming helpers for the MTI Brain analytics pipeline."""

from __future__ import annotations

import re


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


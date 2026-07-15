"""Prompt injection guard — sanitizes user questions before they enter the LLM pipeline."""

from __future__ import annotations

import re

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|preceding)\s+(instructions?|prompts?|context)", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above|preceding)?\s*(instructions?|prompts?|context|rules?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above)?\s*(instructions?|prompts?|context)", re.IGNORECASE),
    re.compile(r"(return|show|print|output|reveal|display)\s+(\w+\s+)*(system\s+prompt|system\s+message|initial\s+prompt|hidden\s+prompt)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|in)\s+", re.IGNORECASE),
    re.compile(r"new\s+instructions?:\s*", re.IGNORECASE),
    re.compile(r"override\s+(previous\s+)?(instructions?|rules?|prompt)", re.IGNORECASE),
    re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
]

_XML_CLOSE_TAGS = re.compile(r"</?(user_question|system|instructions?|prompt|context)>", re.IGNORECASE)


def sanitize_question(text: str) -> str:
    """Sanitize a user question to mitigate prompt injection attacks.

    1. Strips known injection preambles
    2. Removes XML tag injection attempts
    3. Escapes remaining angle brackets so injected tags are neutralized
    """
    cleaned = text

    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    cleaned = _XML_CLOSE_TAGS.sub("", cleaned)

    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")

    cleaned = cleaned.strip()

    if not cleaned:
        return text.replace("<", "&lt;").replace(">", "&gt;").strip()

    return cleaned

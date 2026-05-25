"""Node name constants and model tier mapping for the analytics pipeline.

Single source of truth for node identifiers shared between graph.py (routing),
token_tracker.py (cost attribution), and any other module that references nodes by name.
"""

from __future__ import annotations

# ── Node name constants ────────────────────────────────────────────────────────

INTAKE = "intake_classifier"
GENERAL_CHAT = "general_chat"
CONTEXT_FETCHER = "context_fetcher"
INTENT_RESOLVER = "intent_resolver"
CLARIFICATION = "clarification"
QUERY_COMPILER = "query_compiler"
FILTER_RESOLVER = "filter_resolver"
SQL_VALIDATOR = "sql_validator"
EXECUTOR = "executor"
SYNTHESIS = "synthesis"
CHART_AGENT = "chart_agent"
ERROR_RESPONSE = "error_response"

ALL_NODES = (
    INTAKE,
    GENERAL_CHAT,
    CONTEXT_FETCHER,
    INTENT_RESOLVER,
    CLARIFICATION,
    QUERY_COMPILER,
    FILTER_RESOLVER,
    SQL_VALIDATOR,
    EXECUTOR,
    SYNTHESIS,
    CHART_AGENT,
    ERROR_RESPONSE,
)

# ── Model tier per node ────────────────────────────────────────────────────────
# "fast"     → Haiku 4.5   (classification, simple text, chart labels)
# "balanced" → Sonnet 4.6  (intent extraction, decomposition, synthesis)
# "deep"     → Opus 4.7    (SQL repair — semantic boundary preservation)
# "none"     → no LLM call (deterministic nodes)

NODE_TIER: dict[str, str] = {
    INTAKE:           "fast",
    GENERAL_CHAT:     "fast",
    CLARIFICATION:    "fast",
    CHART_AGENT:      "fast",
    CONTEXT_FETCHER:  "none",
    INTENT_RESOLVER:  "balanced",
    QUERY_COMPILER:   "balanced",
    FILTER_RESOLVER:  "fast",
    SQL_VALIDATOR:    "none",
    EXECUTOR:         "deep",
    SYNTHESIS:        "balanced",
    ERROR_RESPONSE:   "none",
}

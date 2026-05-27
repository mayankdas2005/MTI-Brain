"""AnalyticsState TypedDict for the Neo4j analytics LangGraph pipeline.

A single AnalyticsState carries all fields for the 10-node pipeline.
Nodes only populate the fields they produce; unset fields remain at defaults.
"""

from __future__ import annotations

from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AnalyticsState(TypedDict):
    # ── Core conversation ────────────────────────────────────────────────────
    messages: Annotated[list, add_messages]
    user_id: str
    thread_id: str
    persona: str                          # executive | analyst | manager — passed in from HTTP, NOT re-detected
    question: str

    # ── Routing ──────────────────────────────────────────────────────────────
    question_type: str                    # general_chat | analytics
    decompose_needed: bool
    needs_clarification: bool
    clarification_count: int              # max 2 per turn
    clarification_reason: str | None

    # ── Pipeline ─────────────────────────────────────────────────────────────
    semantic_context: dict | None         # output of context_fetcher
    resolved_intent: dict | None          # output of intent_resolver
    semantic_ir_list: list[dict]          # list of SemanticIR (1 for simple, N for decomposed)
    sql_list: list[str]                   # compiled SQL per SemanticIR
    failed_sql_indices: list[int]         # indices of sql_list entries that failed validation
    recompile_count: int                  # max 1
    repair_count: int                     # max 2 across all repair types
    filter_resolution_needed: bool

    # ── Execution ────────────────────────────────────────────────────────────
    result_list: list[dict]               # raw query results per sub-query
    query_summary: dict | None            # QuerySummary object
    no_data: bool
    reliability_flags: list[str]
    low_confidence_filters: list[dict]
    zero_row_probe_result: str | None     # human-readable explanation from Z2/Z3 probe

    # ── Output ───────────────────────────────────────────────────────────────
    answer: str
    chart_spec: dict | None
    follow_ups: list[str]

    # ── Memory / feedback ────────────────────────────────────────────────────
    feedback_context: str
    summary: str                          # short-term session summary

    # ── Error / control ──────────────────────────────────────────────────────
    error: str | None
    execution_error: str | None          # DB-level error surfaced by executor; fed to intent_resolver for semantic re-interpretation
    _prev_repair_count: int              # last repair_count at executor completion; detects new repairs in route_executor
    stopped: bool
    deep_analysis: bool
    max_rows: int                         # user-configured SQL row limit (default 100, applied as LIMIT in executor)

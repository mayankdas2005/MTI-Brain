"""AnalyticsState TypedDict for the Neo4j analytics LangGraph pipeline.

A single AnalyticsState carries all fields for the 13-node pipeline.
Nodes only populate the fields they produce; unset fields remain at defaults.
"""

from __future__ import annotations

import operator
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
    needs_clarification: bool
    clarification_count: int              # max 2 per turn
    clarification_reason: str | None

    # ── Pipeline ─────────────────────────────────────────────────────────────
    semantic_context: dict | None         # output of context_fetcher (Phase 1: tables + Group A, no columns)
    enriched_schema: dict | None          # output of schema_enricher — complete columns for anchor_tables only
    anchor_tables_resolved: list[str]     # output of anchor_resolver — 2-4 anchor tables before ir_builder
    specialist_outputs: Annotated[list[dict], operator.add]  # accumulates from 3 parallel specialists via Send
    resolved_intent: dict | None          # output of intent_assembler (same format as old intent_resolver)
    intent_directive: str | None              # raw directive from intent_resolver <directive> tag
    intent_directive_instructions: str | None  # <instructions> sub-section: SQL execution requirements
    intent_directive_context: str | None       # <context> sub-section: structural guidance, informational
    filter_directive: str | None          # resolved filter list from filter_resolver (DB codes + confidence)
    schema_directive: str | None          # code-verified structure from ir_builder (tables, joins, measures)
    semantic_ir_list: list[dict]          # list of SemanticIR (always 1 — decomposition removed)
    sql_list: list[str]                   # compiled SQL (always 1 entry)
    recompile_count: int                  # max 1
    repair_count: int                     # max 2 across all repair types
    filter_resolution_needed: bool
    repair_history: list[dict]            # [{attempt, error, sql_fingerprint|sql_fragment}] — prevents circular repair

    # ── Execution ────────────────────────────────────────────────────────────
    result_list: list[dict]               # raw query results per sub-query
    query_summary: dict | None            # QuerySummary object
    no_data: bool
    reliability_flags: list[str]
    low_confidence_filters: list[dict]
    zero_row_probe_result: str | None     # human-readable explanation from Z2/Z3 probe
    zero_row_rewrite_count: int           # tracks zero-row repair attempts (max 1) to prevent infinite loops

    # ── Data quality (pre-synthesis gate) ────────────────────────────────────
    data_quality_flag: bool               # True if DATA_INTEGRITY_GATE triggered by data_quality_checker
    data_quality_reason: str | None       # One-sentence reason from data_quality_checker (shown as ### Data Quality Concern)

    # ── Output ───────────────────────────────────────────────────────────────
    answer: str
    chart_spec: dict | None
    chart_type: str | None
    alternative_chart_specs: list[dict]    # full Vega-Lite specs — each: {"chart_type": str, "spec": dict}
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

    # ── Audit / lineage ───────────────────────────────────────────────────────
    user_email: str | None               # caller email — written to execution log
    pipeline_start_ms: float             # time.perf_counter() at pipeline entry — used to compute duration_ms in audit log
    current_date: str                    # ISO date (YYYY-MM-DD) at pipeline start — synthesis uses this for temporal calculations
    pattern_matched: bool                # set True by query_compiler when a QueryPattern is found and used
    pattern_name: str | None             # intent of the top matched pattern
    is_retry: bool                       # True when triggered from /retry or /edit endpoint
    prior_sql: str | None               # SQL from the immediately prior pipeline run; set by sql_generator; used by intent_resolver and sql_generator on retry/error
    prior_question: str | None          # original question text looked up from DB; used by context_fetcher to find the right tables for refinements
    prior_sql_tables: list[str]         # schema.table FQNs parsed from prior_sql (e.g. ["lpp.counterparty_exposure"])
    is_refinement: bool                 # True for user-initiated refinements (prior_sql from frontend, not is_retry)

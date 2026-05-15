"""Shared state schema for LangGraph agent pipelines.

Each agent graph receives and returns an AgentState dict.  Fields are
cumulative — a node adds its outputs and passes the full state to the
next node.  Optional fields are None until the relevant node runs.
"""

from __future__ import annotations

import uuid
from typing import TypedDict


class AgentState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────────────────
    question: str
    thread_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    deep_analysis: bool

    # ── Classification ──────────────────────────────────────────────────────
    question_type: str          # "data_query" | "conversational" | ...
    intent: str                 # short intent label

    # ── SQL generation ─────────────────────────────────────────────────────
    sql: str | None
    sql_reasoning: str | None

    # ── Execution ──────────────────────────────────────────────────────────
    columns: list[str] | None
    rows: list[list] | None
    row_count: int | None
    source_tables: list[str] | None
    data_freshness_at: str | None

    # ── Visualisation ──────────────────────────────────────────────────────
    chart_spec: dict | None

    # ── Response ───────────────────────────────────────────────────────────
    answer: str | None
    follow_ups: list[str] | None
    reasoning: str | None

    # ── Pipeline metadata ──────────────────────────────────────────────────
    pipeline_steps: list[dict] | None
    duration_ms: int | None
    stopped: bool

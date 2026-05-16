"""Shared state schema for the MTI Brain LangGraph pipeline.

A single State class carries all fields for both the inner 14-node pipeline
(simple/complex questions) and the outer Plan/Execute/Reflect/Repair loop
(advanced questions). Nodes only populate the fields they produce; unset
fields remain at their defaults.
"""

from __future__ import annotations

import operator
from typing import Annotated

from langgraph.graph import MessagesState


def _scratchpad_reducer(left: dict, right: dict) -> dict:
    """Merge outer-loop scratchpad dicts (update, not replace)."""
    if not left:
        return right or {}
    merged = dict(left)
    merged.update(right or {})
    return merged


class State(MessagesState):
    # ── Input / Classification ──────────────────────────────────────────────
    question: str
    question_type: str          # kg_query | general_chat | rejected
    persona: str                # Analyst-F | Manager-F | Director-F | Executive-F
    complexity: str             # simple | complex | advanced
    intent: str                 # balance_lookup | fx_exposure | counterparty_risk | …
    routing: str                # kg_only | kg_tribal | hil

    # ── Ontology Resolution ─────────────────────────────────────────────────
    ontology_terms: list[dict]  # [{uri, label, type, property_type}]

    # ── SPARQL Generation / Validation / Execution ──────────────────────────
    sparql: str
    sparql_error: str
    sparql_retries: int
    kg_results: list[dict]      # raw Fuseki bindings [{var: {value, type}, …}]
    kg_columns: list[str]       # variable names from head.vars
    kg_rows: list[list]         # extracted scalar values, row-major
    kg_row_count: int

    # ── Tribal Graph (GraphRAG) ──────────────────────────────────────────────
    tribal_facts: Annotated[list[dict], operator.add]

    # ── Graph Reasoning / Evidence ───────────────────────────────────────────
    reasoning: str
    evidence: Annotated[list[str], operator.add]

    # ── Answer / Visualisation ───────────────────────────────────────────────
    answer: str
    chart_json: dict | None     # raw spec from CHART_PROMPT (before data population)
    viz_spec: dict | None       # fully populated chart spec (after _build_chart_data)
    follow_ups: list[str]

    # ── Governance / HIL ────────────────────────────────────────────────────
    hil_required: bool
    hil_approved: bool | None
    governance_halt: str | None

    # ── Conversation History ─────────────────────────────────────────────────
    summary: str

    # ── Pipeline Metadata ────────────────────────────────────────────────────
    pipeline_steps: list[dict]
    _thread_id: str
    _user_id: str
    stopped: bool

    # ── Outer Loop: Plan / Execute / Reflect / Repair ────────────────────────
    plan: dict                          # {nodes: [SubQ], edges: [(id, id)], budget: {}}
    sub_questions: list[dict]           # [{id, question, depends_on, status, bindings,
                                        #   sparql, error, attempt, l1_count, l2_count}]
    scratchpad: Annotated[dict, _scratchpad_reducer]  # {sub_q_id: {status, answer, …}}
    plan_attempts: int
    budget_used: dict                   # {tokens, seconds, fuseki_rows, usd}
    halt_reason: str | None
    final_reflection: str

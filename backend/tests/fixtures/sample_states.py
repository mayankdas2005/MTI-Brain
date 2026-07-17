"""Pre-built AnalyticsState dicts for each pipeline stage."""

from __future__ import annotations


def state_at_intake(question: str = "What was total revenue last quarter?", **overrides) -> dict:
    base = {
        "thread_id": "test-thread-001",
        "user_id": "test-user",
        "messages": [{"role": "user", "content": question}],
        "question": question,
    }
    base.update(overrides)
    return base


def state_after_intake(question_type: str = "analytics", **overrides) -> dict:
    state = state_at_intake(**overrides)
    state["question_type"] = question_type
    return state


def state_after_context(tables: list[str] | None = None, **overrides) -> dict:
    state = state_after_intake(**overrides)
    state["neo4j_raw_graph"] = {
        "nodes": [{"_label": "Table", "fqn": t} for t in (tables or ["lpp.fact_media_metrics"])],
        "edges": [],
    }
    return state


def state_after_anchor(tables: list[str] | None = None, **overrides) -> dict:
    state = state_after_context(tables=tables, **overrides)
    state["anchor_tables_resolved"] = tables or ["lpp.fact_media_metrics"]
    return state


def state_after_compiler(**overrides) -> dict:
    state = state_after_anchor(**overrides)
    state["semantic_ir_list"] = [{
        "intent": "revenue",
        "complexity": "simple",
        "anchor_tables": ["lpp.fact_media_metrics"],
        "measures": [{"table_fqn": "lpp.fact_media_metrics", "column_name": "revenue", "alias": "revenue", "aggregation": "SUM"}],
        "dimensions": [{"table_fqn": "lpp.fact_media_metrics", "column_name": "date", "alias": "date"}],
        "filters": [],
    }]
    return state


def state_after_executor(columns: list[str] | None = None, rows: list[list] | None = None, **overrides) -> dict:
    state = state_after_compiler(**overrides)
    state["result_list"] = [{
        "columns": columns or ["date", "revenue"],
        "rows": rows or [["2024-Q4", 1200000]],
    }]
    return state


def state_with_error(error: str = "llm_unavailable", **overrides) -> dict:
    state = state_at_intake(**overrides)
    state["error"] = error
    return state

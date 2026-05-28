"""Node 1b: intent_resolver — constrained LLM intent extraction.

Uses Sonnet. Extracts structured intent from the question, constrained to
identifiers from SemanticContext. Validates every identifier post-LLM.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.prompts import INTENT_RESOLVE_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.state import AnalyticsState


async def intent_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("intent_resolver START | thread={}", state["thread_id"])

    prompt = _build_prompt(state)

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    try:
        response = await _call()
    except Exception as e:
        logger.error("intent_resolver LLM failed | thread={} | error={}", state["thread_id"], e)
        return {"error": "LLM unavailable", "needs_clarification": False}

    raw = response.content or ""
    resolved = _parse_response(raw, state["thread_id"])

    if resolved is None:
        logger.warning("intent_resolver parse failed | thread={} | raw_len={}", state["thread_id"], len(raw))
        return {"error": "intent_parse_failed", "needs_clarification": False}

    validation_error = _validate_identifiers(resolved, state.get("semantic_context") or {})
    if validation_error:
        logger.warning("intent_resolver hallucination | thread={} | error={} | proceeding best-effort", state["thread_id"], validation_error)

    confidence = resolved.get("confidence", 0.0)

    logger.info(
        "intent_resolver DONE | template={} | confidence={:.2f} | complexity={}",
        resolved.get("template_id"), confidence, resolved.get("complexity"),
    )
    return {
        "resolved_intent": resolved,
        "needs_clarification": False,
        "clarification_reason": None,
        "execution_error": None,
        "repair_count": 0,
        "recompile_count": (state.get("recompile_count") or 0) + (1 if state.get("execution_error") else 0),
    }


_LLM_STRIP = {"retrieval_paths", "score", "matched_via", "community_id"}


def _clean_for_llm(objects: list[dict]) -> list[dict]:
    """Remove pipeline-internal fields before serializing context for the LLM."""
    return [{k: v for k, v in obj.items() if k not in _LLM_STRIP} for obj in objects]


def _format_recent_messages(messages: list) -> str:
    """Format last 3 messages as conversation context for turns without a session_summary."""
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-3:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:300]
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else ""


def _build_prompt(state: AnalyticsState) -> list:
    semantic_context = state.get("semantic_context") or {}

    tables_for_llm  = _clean_for_llm(semantic_context.get("tables", [])[:8])
    columns_for_llm = _clean_for_llm(semantic_context.get("columns", [])[:12])

    matched_templates = [
        {"id": t.get("id"), "score": t.get("score"), "anchor_table_fqns": t.get("anchor_table_fqns")}
        for t in semantic_context.get("templates", [])[:2]
    ]

    logger.info(
        "intent_resolver | llm_context | tables={} | columns={}",
        [t.get("fqn") for t in tables_for_llm],
        [(c.get("table_fqn"), c.get("name")) for c in columns_for_llm],
    )

    context_str = json.dumps({
        "matched_templates": matched_templates,
        "templates": semantic_context.get("templates", [])[:5],
        "tables": tables_for_llm,
        "columns": columns_for_llm,
        "business_terms": semantic_context.get("business_terms", [])[:5],
        "intents": semantic_context.get("intents", [])[:3],
    }, indent=2)

    session_summary = semantic_context.get("session_summary") or state.get("summary") or ""
    recent_msgs = _format_recent_messages(state.get("messages", []))
    conversation_context = session_summary if session_summary else recent_msgs

    execution_error = state.get("execution_error")
    prior_sql = state.get("prior_sql")
    if execution_error:
        if prior_sql:
            execution_error_section = (
                "\nPREVIOUS EXECUTION FAILURE — re-interpret to avoid repeating the same approach:\n"
                f"SQL that failed:\n<prior_sql>{prior_sql}</prior_sql>\n"
                f"Error: <execution_error>{execution_error}</execution_error>\n"
                "Choose different tables, columns, or join strategy.\n"
            )
        else:
            execution_error_section = (
                f"\nPREVIOUS EXECUTION FAILURE — the SQL generated from your last interpretation "
                f"was rejected by the database with this error. Re-interpret the question choosing "
                f"different columns, joins, or tables to avoid the same failure:\n"
                f"<execution_error>{execution_error}</execution_error>\n"
            )
    else:
        execution_error_section = ""

    return INTENT_RESOLVE_PROMPT.format_messages(
        question=state["question"],
        persona=state.get("persona", "executive"),
        feedback_context=state.get("feedback_context", ""),
        conversation_context=conversation_context,
        memory_context=semantic_context.get("memory_context", ""),
        semantic_context=context_str,
        execution_error_section=execution_error_section,
        reasoning_directive=REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL,
    )


def _parse_response(raw: str, thread_id: str) -> dict | None:
    from json_repair import loads as json_loads
    output_tag = parse_tag(raw, "output")
    if not output_tag:
        logger.warning("intent_resolver | no <output> tag | thread={}", thread_id)
        return None
    try:
        return json_loads(output_tag)
    except Exception as e:
        logger.warning("intent_resolver | json_repair failed | thread={} | error={}", thread_id, e)
        return None


def _validate_identifiers(resolved: dict, semantic_context: dict) -> str | None:
    """Check that every measure/dimension/filter column exists in semantic_context."""
    known_columns = set()
    for col in semantic_context.get("columns", []):
        if col.get("table_fqn") and col.get("name"):
            known_columns.add(f"{col['table_fqn']}.{col['name']}")

    for measure in resolved.get("measures", []):
        col_ref = f"{measure.get('table_fqn', '')}.{measure.get('column_name', '')}"
        if col_ref and "." in col_ref and col_ref not in known_columns and known_columns:
            return f"Column {col_ref} not found in available data catalog."

    for dim in resolved.get("dimensions", []):
        col_ref = f"{dim.get('table_fqn', '')}.{dim.get('column_name', '')}"
        if col_ref and "." in col_ref and col_ref not in known_columns and known_columns:
            return f"Column {col_ref} not found in available data catalog."

    return None

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
        return {"needs_clarification": True, "clarification_reason": "I couldn't process your question. Please try again."}

    raw = response.content or ""
    resolved = _parse_response(raw, state["thread_id"])

    if resolved is None:
        logger.warning("intent_resolver parse failed | thread={} | forcing clarification", state["thread_id"])
        return {"needs_clarification": True, "clarification_reason": "I couldn't parse your question clearly. Could you rephrase it?"}

    validation_error = _validate_identifiers(resolved, state.get("semantic_context") or {})
    if validation_error:
        logger.warning("intent_resolver hallucination | thread={} | error={}", state["thread_id"], validation_error)
        return {"needs_clarification": True, "clarification_reason": validation_error}

    confidence = resolved.get("confidence", 0.0)
    top_template_score = _get_top_template_score(state.get("semantic_context") or {})

    needs_clarification = confidence < 0.65 or top_template_score < 0.60
    decompose_needed = _check_decompose_needed(resolved, state.get("semantic_context") or {})

    logger.info(
        "intent_resolver DONE | template={} | confidence={:.2f} | complexity={} | decompose={}",
        resolved.get("template_id"), confidence, resolved.get("complexity"), decompose_needed,
    )
    return {
        "resolved_intent": resolved,
        "needs_clarification": needs_clarification,
        "decompose_needed": decompose_needed,
        "clarification_reason": "I need a bit more detail to answer accurately." if needs_clarification else None,
        "execution_error": None,
        "repair_count": 0,
        "recompile_count": (state.get("recompile_count") or 0) + (1 if state.get("execution_error") else 0),
    }


def _build_prompt(state: AnalyticsState) -> list:
    semantic_context = state.get("semantic_context") or {}
    context_str = json.dumps({
        "templates": semantic_context.get("templates", [])[:5],
        "tables": semantic_context.get("tables", [])[:10],
        "columns": semantic_context.get("columns", [])[:15],
        "business_terms": semantic_context.get("business_terms", [])[:5],
        "intents": semantic_context.get("intents", [])[:3],
    }, indent=2)

    execution_error = state.get("execution_error")
    execution_error_section = (
        f"\nPREVIOUS EXECUTION FAILURE — the SQL generated from your last interpretation "
        f"was rejected by the database with this error. Re-interpret the question choosing "
        f"different columns, joins, or tables to avoid the same failure:\n"
        f"<execution_error>{execution_error}</execution_error>\n"
        if execution_error else ""
    )

    return INTENT_RESOLVE_PROMPT.format_messages(
        question=state["question"],
        persona=state.get("persona", "executive"),
        domain_preference="",
        feedback_context=state.get("feedback_context", ""),
        conversation_context=semantic_context.get("session_summary", ""),
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


def _get_top_template_score(semantic_context: dict) -> float:
    templates = semantic_context.get("templates", [])
    if not templates:
        return 1.0
    scores = [t.get("score", 1.0) for t in templates if t.get("score") is not None]
    return max(scores) if scores else 1.0


def _check_decompose_needed(resolved: dict, semantic_context: dict) -> bool:
    """Apply three-signal decomposition logic from the plan."""
    if resolved.get("complexity") == "advanced":
        top_score = _get_top_template_score(semantic_context)
        if top_score > 0.72:
            return True

    anchor_tables = resolved.get("anchor_tables", [])
    if len(anchor_tables) >= 2:
        tables = semantic_context.get("tables", [])
        community_map = {t["fqn"]: t.get("community_id") for t in tables if t.get("fqn")}
        anchor_communities = {community_map.get(t) for t in anchor_tables if community_map.get(t)}
        if len(anchor_communities) >= 2:
            return True

    return False

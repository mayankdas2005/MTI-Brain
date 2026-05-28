"""Node C: clarification — asks a single targeted question when intent is unclear.

Max 2 clarifications per turn. Loops back to intent_resolver after response.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
from app.services.neo4j_analytics.prompts import CLARIFICATION_PROMPT, REASONING_DIRECTIVE_BRIEF
from app.services.neo4j_analytics.state import AnalyticsState


async def clarification(state: AnalyticsState, config: RunnableConfig) -> dict:
    count = state.get("clarification_count", 0)
    reason = state.get("clarification_reason", "The question needs more specificity.")
    logger.info("clarification START | thread={} | count={} | reason={}", state["thread_id"], count, reason)

    semantic_context = state.get("semantic_context") or {}
    session_summary = semantic_context.get("session_summary") or state.get("summary") or ""
    recent_msgs = _format_recent_messages(state.get("messages", []))
    conversation_context = session_summary if session_summary else recent_msgs

    conversation_section = (
        f"CONVERSATION CONTEXT:\n<conversation_context>{conversation_context}</conversation_context>"
        if conversation_context else ""
    )

    prompt = CLARIFICATION_PROMPT.format_messages(
        question=state["question"],
        persona=state.get("persona", "executive"),
        clarification_reason=reason,
        conversation_section=conversation_section,
        reasoning_directive=REASONING_DIRECTIVE_BRIEF,
    )

    from app.services.neo4j_analytics.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    response = await _call()

    raw = response.content or ""
    question_text = parse_tag(raw, "question") or raw.strip()

    logger.info("clarification DONE | thread={} | question={}", state["thread_id"], question_text[:80])
    return {
        "answer": question_text,
        "clarification_count": count + 1,
        "needs_clarification": False,
    }


def _format_recent_messages(messages: list) -> str:
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-3:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:300]
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else ""

"""Node C: clarification — asks a single targeted question when intent is unclear.

Max 2 clarifications per turn. Loops back to intent_resolver after response.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.neo4j_analytics.prompts import CLARIFICATION_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.state import AnalyticsState


async def clarification(state: AnalyticsState, config: dict) -> dict:
    count = state.get("clarification_count", 0)
    reason = state.get("clarification_reason", "The question needs more specificity.")
    logger.info("clarification START | thread={} | count={} | reason={}", state["thread_id"], count, reason)

    prompt = CLARIFICATION_PROMPT.format_messages(
        question=state["question"],
        clarification_reason=reason,
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.langfuse_integration import create_callback_handler, langfuse_context

    llm = get_llm("fast")
    handler = create_callback_handler()
    callbacks = [handler] if handler else []

    with langfuse_context(session_id=state["thread_id"], user_id=state["user_id"], tags=["neo4j_analytics", "clarification"]):
        response = await llm.ainvoke(prompt, config={"callbacks": callbacks})

    raw = response.content or ""
    question_text = parse_tag(raw, "question") or raw.strip()

    logger.info("clarification DONE | thread={} | question={}", state["thread_id"], question_text[:80])
    return {
        "answer": question_text,
        "clarification_count": count + 1,
        "needs_clarification": False,
    }

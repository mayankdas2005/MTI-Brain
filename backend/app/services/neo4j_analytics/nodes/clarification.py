"""Node C: clarification — asks a single targeted question when intent is unclear.

Max 2 clarifications per turn. Loops back to intent_resolver after response.
"""

from __future__ import annotations

from app.core.logger import logger
from app.services.neo4j_analytics.helpers import parse_tag
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

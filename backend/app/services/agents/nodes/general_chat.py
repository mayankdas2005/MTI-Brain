"""Node G: general_chat — conversational response for non-analytics questions."""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import GENERAL_CHAT_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


async def general_chat(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("general_chat START | thread={}", state["thread_id"])

    session_summary = state.get("summary") or ""
    feedback_context = state.get("feedback_context") or ""
    semantic_context = state.get("semantic_context") or {}
    memory_context = semantic_context.get("memory_context") or ""

    conversation_section = (
        f"CONVERSATION CONTEXT:\n<conversation_context>{session_summary}</conversation_context>"
        if session_summary else ""
    )
    feedback_section = (
        f"USER PREFERENCES (apply silently):\n<feedback_context>{feedback_context}</feedback_context>"
        if feedback_context else ""
    )
    memory_section = (
        f"USER MEMORY (preferences from prior sessions):\n<memory_context>{memory_context}</memory_context>"
        if memory_context else ""
    )

    prompt = GENERAL_CHAT_PROMPT.format_messages(
        question=state["question"],
        persona=state.get("persona", "executive"),
        conversation_section=conversation_section,
        memory_section=memory_section,
        feedback_section=feedback_section,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=config)

    response = await _call()

    raw = response.content or ""
    answer = parse_tag(raw, "answer") or raw.strip()
    follow_ups = _parse_follow_ups(raw)

    logger.info("general_chat DONE | thread={} | answer_len={} | follow_ups={}", state["thread_id"], len(answer), len(follow_ups))
    return {"answer": answer, "follow_ups": follow_ups}


def _parse_follow_ups(raw: str) -> list[str]:
    from json_repair import loads as json_loads
    tag_content = parse_tag(raw, "follow_ups")
    if not tag_content:
        return []
    try:
        result = json_loads(tag_content)
        if isinstance(result, list):
            return [str(q) for q in result[:3]]
    except Exception:
        pass
    return []

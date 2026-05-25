"""Node G: general_chat — conversational response for non-analytics questions."""

from __future__ import annotations

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.neo4j_analytics.prompts import GENERAL_CHAT_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.neo4j_analytics.state import AnalyticsState


async def general_chat(state: AnalyticsState, config: dict) -> dict:
    logger.info("general_chat START | thread={}", state["thread_id"])

    prompt = GENERAL_CHAT_PROMPT.format_messages(
        question=state["question"],
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.langfuse_integration import create_callback_handler, langfuse_context

    llm = get_llm("fast")
    handler = create_callback_handler()
    callbacks = [handler] if handler else []

    with langfuse_context(session_id=state["thread_id"], user_id=state["user_id"], tags=["neo4j_analytics", "general_chat"]):
        response = await llm.ainvoke(prompt, config={"callbacks": callbacks})

    raw = response.content or ""
    answer = parse_tag(raw, "answer") or raw.strip()
    follow_ups = _parse_follow_ups(raw)

    logger.info("general_chat DONE | thread={} | answer_len={}", state["thread_id"], len(answer))
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

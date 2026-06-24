"""Node G: general_chat — conversational response for non-analytics questions."""

from __future__ import annotations
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_mission_context, parse_tag
from app.services.agents.prompts import GENERAL_CHAT_HUMAN, GENERAL_CHAT_SYSTEM
from app.services.agents.state import AnalyticsState


async def general_chat(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("general_chat START | thread={}", state["thread_id"])

    session_summary = state.get("summary") or ""
    from app.services.chat.feedback import build_feedback_context_for_node as _fb_for_node
    feedback_context = _fb_for_node(state.get("feedback_context") or [], "general")
    semantic_context = state.get("semantic_context") or {}
    memory_context = semantic_context.get("memory_context") or ""
    global_instructions = state.get("global_instructions") or ""

    # Use DB-loaded conversation_history (bypasses checkpoint messages)
    conversation_context = state.get("conversation_history") or session_summary or ""
    conversation_section = (
        f"CONVERSATION CONTEXT:\n<conversation_context>{conversation_context}</conversation_context>"
        if conversation_context and conversation_context != "(no prior context)" else ""
    )
    instructions_section = (
        f"<user_instructions>\nApply only instructions relevant to your task as a conversational assistant. These are explicit user-defined rules — follow them precisely. When an instruction conflicts with learned feedback, follow the instruction; where possible, also satisfy the feedback's intent without violating the rule.\n{global_instructions}\n</user_instructions>"
        if global_instructions else ""
    )
    feedback_section = (
        f"LEARNED PREFERENCES (from past feedback — apply within the bounds of standing instructions above):\n<feedback_context>{feedback_context}</feedback_context>"
        if feedback_context else ""
    )
    memory_section = (
        f"USER MEMORY (preferences from prior sessions):\n<memory_context>{memory_context}</memory_context>"
        if memory_context else ""
    )

    prompt = [
        SystemMessage(
            content=GENERAL_CHAT_SYSTEM.format(
                persona=state.get("persona", "analyst"),
                instructions_section=instructions_section,
                conversation_section=conversation_section,
                memory_section=memory_section,
                feedback_section=feedback_section,
            )
        ),
        HumanMessage(content=GENERAL_CHAT_HUMAN.format(question=state["question"])),
    ]
    _mission = build_mission_context(
        state,
        role="Answer a non-analytics question conversationally and helpfully",
        feeds="user (direct visible answer)",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-general-chat", max_attempts=2, backoff_base=5.0)

    response = await _call()

    raw = response.content or ""
    answer = parse_tag(raw, "answer") or raw.strip()
    follow_ups = _parse_follow_ups(raw)

    logger.info("general_chat DONE | thread={} | answer_len={} | follow_ups={}", state["thread_id"], len(answer), len(follow_ups))
    result: dict = {"answer": answer, "follow_ups": follow_ups}
    if not state.get("is_retry"):
        result["messages"] = [HumanMessage(content=state["question"]), AIMessage(content=answer)]
    return result


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

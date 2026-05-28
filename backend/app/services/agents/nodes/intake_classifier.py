"""Node 0: intake_classifier — routes general_chat vs analytics.

Uses Haiku for speed. No <reasoning> output — not displayed in UI.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import INTAKE_CLASSIFY_PROMPT
from app.services.agents.state import AnalyticsState


async def intake_classifier(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("intake_classifier START | thread={} | question={}", state["thread_id"], state["question"][:80])

    conversation_context = _format_conversation(state.get("messages", []), state.get("summary") or "")
    prompt = INTAKE_CLASSIFY_PROMPT.format_messages(
        question=state["question"],
        conversation_context=conversation_context,
    )

    result = await _call_llm(prompt, config)
    question_type = _parse_type(result)

    logger.info("intake_classifier DONE | thread={} | type={}", state["thread_id"], question_type)
    return {"question_type": question_type}


async def _call_llm(prompt, config: RunnableConfig) -> str:
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    merged_config = dict(config)
    merged_config["tags"] = list(merged_config.get("tags", [])) + ["no_stream"]

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=merged_config)

    response = await _call()
    return response.content or ""


def _parse_type(raw: str) -> str:
    from json_repair import loads as json_loads
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        qtype = data.get("type", "analytics")
        if qtype not in ("general_chat", "analytics"):
            return "analytics"
        return qtype
    except Exception:
        return "analytics"


def _format_conversation(messages: list, session_summary: str = "") -> str:
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-3:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:200]
        lines.append(f"{role}: {content}")
    # If compress has deleted old messages, the summary carries the prior context
    if not lines and session_summary:
        return f"(prior conversation summary): {session_summary[:400]}"
    return "\n".join(lines) if lines else "(no prior context)"

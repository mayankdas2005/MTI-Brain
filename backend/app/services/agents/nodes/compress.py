"""compress — rolling conversation history summarizer.

Triggered when len(messages) >= SUMMARIZE_THRESHOLD (6).
Summarizes all messages except the most recent pair, removes the originals via
RemoveMessage so the Postgres checkpoint stays lean, and writes the summary to
both state["summary"] (persisted across turns) and Redis (30 min TTL).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import COMPRESS_HUMAN, COMPRESS_SYSTEM
from app.services.agents.state import AnalyticsState

SUMMARIZE_THRESHOLD = 6


async def compress(state: AnalyticsState, config: RunnableConfig) -> dict:
    thread_id = state["thread_id"]
    logger.info("compress START | thread={}", thread_id)

    messages = state.get("messages") or []
    to_summarize = messages[:-2]

    if not to_summarize:
        logger.info("compress | nothing to summarize | thread={}", thread_id)
        return {}

    exchanges = _build_exchanges(to_summarize)
    existing = state.get("summary") or ""
    existing_section = f"Previous summary:\n{existing}" if existing else "None."

    from app.core.retry import retry_async
    try:
        prompt = [
            SystemMessage(content=COMPRESS_SYSTEM),
            HumanMessage(
                content=COMPRESS_HUMAN.format(
                    existing_summary_section=existing_section,
                    recent_exchanges="\n\n".join(exchanges),
                )
            ),
        ]
        raw = await retry_async(lambda: get_llm("fast").ainvoke(prompt, config=config), service="bedrock-compress", max_attempts=2, backoff_base=5.0)
        text = raw.content if hasattr(raw, "content") else str(raw)
        summary = parse_tag(text, "summary") or text.strip()
    except Exception as e:
        logger.warning("compress | LLM failed | thread={} | error={}", thread_id, e)
        return {}

    if summary:
        logger.info("compress | summary generated | thread={} | len={}", thread_id, len(summary))

    logger.info("compress DONE | thread={} | removed={} | summary_len={}",
                thread_id, len(to_summarize), len(summary))

    return {
        "summary": summary,
        "messages": [
            RemoveMessage(id=m.id)
            for m in to_summarize
            if hasattr(m, "id") and m.id
        ],
    }


def _build_exchanges(messages: list) -> list[str]:
    exchanges = []
    for msg in messages:
        text = _message_text(msg)
        if not text:
            continue
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            exchanges.append(f"Q: {text[:400]}")
        elif isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
            exchanges.append(f"A: {text[:400]}")
    return exchanges


def _message_text(msg) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in msg.content
        )
    return str(msg.content)

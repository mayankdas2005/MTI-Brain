"""compress_node — rolling conversation history summarizer.

Mirrors quest's compress_node exactly. Triggered when len(messages) >= SUMMARIZE_THRESHOLD.
Summarizes all messages except the most recent pair and removes the originals via
RemoveMessage directives.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import SUMMARIZE_PROMPT
from app.services.agents.state import State


def _message_text(msg) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in msg.content
        )
    return str(msg.content)


async def compress_node(state: State) -> dict:
    existing = state.get("summary", "")
    messages = state.get("messages", [])
    to_summarize = messages[:-2]

    exchanges = []
    for msg in to_summarize:
        text = _message_text(msg)
        if isinstance(msg, HumanMessage):
            exchanges.append(f"Q: {text}")
        elif isinstance(msg, AIMessage):
            exchanges.append(f"A: {text}")

    chain = SUMMARIZE_PROMPT | get_llm("balanced")
    raw = await chain.ainvoke({
        "existing_summary_section": f"Previous:\n{existing}" if existing else "None.",
        "recent_exchanges": "\n\n".join(exchanges),
    })
    text = raw.content if hasattr(raw, "content") else str(raw)

    return {
        "summary": parse_tag(text, "summary") or text.strip(),
        "messages": [
            RemoveMessage(id=m.id)
            for m in to_summarize
            if hasattr(m, "id") and m.id
        ],
    }

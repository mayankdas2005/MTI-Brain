"""intake_classify_node — classify question type, persona, and complexity."""

from __future__ import annotations

import asyncio
import json
import re
import time

from langchain_core.messages import HumanMessage

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag, parse_json_from_response, _format_recent_messages
from app.services.agents.ontology_loader import get_class_names_summary
from app.services.agents.prompts import INTAKE_CLASSIFY_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State

_GREETING_RE = re.compile(
    r"^(hi+|hello+|hey+|hiya|howdy|greetings|good\s+(morning|afternoon|evening|day)"
    r"|thanks?(\s+you)?|thank\s+you|cheers|bye+|goodbye|see\s+you"
    r"|what\s+can\s+you\s+do|who\s+are\s+you|what\s+are\s+you|help"
    r"|how\s+are\s+you|nice\s+to\s+meet\s+you|yo+|sup)\W*$",
    re.IGNORECASE,
)


def _is_obvious_general_chat(question: str) -> bool:
    return len(question) <= 80 and bool(_GREETING_RE.match(question.strip()))


async def _fetch_cross_thread_context(question: str, user_id: str) -> str:
    """Fetch cross-thread memory. Only called for kg_query questions."""
    try:
        from langgraph.config import get_store
        store = get_store()
        if not store or not user_id:
            return ""
        results = await asyncio.to_thread(
            store.search, (str(user_id), "mti_queries"), query=question, limit=2
        )
        relevant = [r for r in results if (r.score or 0) >= 0.82]
        if not relevant:
            return ""
        from app.core.logger import logger
        logger.info(
            f"[intake] cross-thread memory matched: "
            f"{len(relevant)} past thread(s), "
            f"top score={max(r.score or 0 for r in relevant):.3f}"
        )
        return "\n\n".join(
            f"Past query: {r.value['question']}\nAnswer: {r.value['answer_summary']}"
            + (f"\nSPARQL used: {r.value['sparql'][:300]}" if r.value.get("sparql") else "")
            for r in relevant
        )
    except Exception:
        return ""


async def intake_classify_node(state: State) -> dict:
    question = state.get("question", "")
    summary = state.get("summary", "")
    messages = state.get("messages", [])
    t0 = time.perf_counter()

    updated_messages = list(messages)
    updated_messages.append(HumanMessage(content=question))

    # ── Fast-path: obvious general_chat — skip LLM + embedding entirely ──
    if _is_obvious_general_chat(question):
        step = {
            "node": "intake_classify",
            "label": "Understanding your question",
            "duration_ms": round((time.perf_counter() - t0) * 1000),
        }
        return {
            "question_type": "general_chat",
            "persona": state.get("persona") or "Analyst",
            "complexity": "simple",
            "cross_thread_context": "",
            "messages": updated_messages,
            "pipeline_steps": [step],
        }

    recent = _format_recent_messages(messages, n=6)
    conversation_context = "\n\n".join(filter(None, [summary, recent])) or "None."

    preset = state.get("persona", "")
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    ontology_context = get_class_names_summary() or "Not available."
    chain = INTAKE_CLASSIFY_PROMPT | get_llm("fast")
    raw = await chain.ainvoke({
        "question": question,
        "conversation_context": conversation_context,
        "persona_preset": f"{preset} (use this — do not infer)" if preset else "not set — infer from question phrasing",
        "reasoning_directive": reasoning_directive,
        "ontology_context": ontology_context,
    })
    text = raw.content if hasattr(raw, "content") else str(raw)

    parsed = parse_json_from_response(text)
    question_type = parsed.get("question_type", "kg_query")
    # If the user explicitly set a persona via response_tone, honour it.
    persona = state.get("persona") or parsed.get("persona", "Analyst")
    complexity = parsed.get("complexity", "simple")
    # deep_analysis overrides the classifier's complexity judgement.
    if state.get("deep_analysis"):
        complexity = "advanced"
    # Refinements ("Refine this query") always modify one existing SPARQL query —
    # force simple so they never decompose into sub-questions via plan/executor.
    if state.get("prior_sql"):
        complexity = "simple"

    # ── Cross-thread memory: only for data queries (embedding call is expensive) ──
    cross_thread_context = state.get("cross_thread_context", "")
    if question_type == "kg_query" and not cross_thread_context:
        cross_thread_context = await _fetch_cross_thread_context(question, state.get("_user_id") or "")

    step = {
        "node": "intake_classify",
        "label": "Understanding your question",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }

    return {
        "question_type": question_type,
        "persona": persona,
        "complexity": complexity,
        "cross_thread_context": cross_thread_context,
        "messages": updated_messages,
        "pipeline_steps": [step],
    }


async def general_chat_node(state: State) -> dict:
    """Return a conversational response for non-data questions."""
    question = state.get("question", "")
    summary = state.get("summary", "")
    messages = state.get("messages", [])

    from app.services.agents.prompts import GENERAL_CHAT_PROMPT
    from langchain_core.messages import AIMessage

    recent = _format_recent_messages(messages, n=6)
    conversation_context = "\n\n".join(filter(None, [summary, recent])) or "None."

    chain = GENERAL_CHAT_PROMPT | get_llm("fast")
    raw = await chain.ainvoke({"question": question, "conversation_context": conversation_context})
    text = raw.content if hasattr(raw, "content") else str(raw)

    answer = parse_tag(text, "answer") or text
    follow_ups_raw = parse_tag(text, "follow_ups")
    try:
        follow_ups = json.loads(follow_ups_raw) if follow_ups_raw else []
    except (json.JSONDecodeError, ValueError):
        follow_ups = []

    updated_messages = list(messages) + [AIMessage(content=answer)]
    return {"answer": answer, "follow_ups": follow_ups, "messages": updated_messages}


async def rejected_node(state: State) -> dict:
    """Return a static rejection response for out-of-scope questions."""
    from langchain_core.messages import AIMessage
    answer = (
        "This question falls outside MTI Brain's scope. I can help with treasury positions, "
        "FX forwards, bank account balances, counterparty exposure, and investment analytics "
        "from the LPP Knowledge Graph. Please rephrase or ask about one of those topics."
    )
    messages = list(state.get("messages", [])) + [AIMessage(content=answer)]
    return {
        "answer": answer,
        "follow_ups": [
            "What is our total counterparty exposure to JPMorgan?",
            "Show me the investment positions for Company ABC.",
            "What FX forwards mature this week?",
        ],
        "messages": messages,
    }

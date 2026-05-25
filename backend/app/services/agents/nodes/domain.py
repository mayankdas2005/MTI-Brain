"""domain_specialist_node — determine intent and routing for a kg_query."""

from __future__ import annotations

import time

from backend.app.services.neo4j_analytics.bedrock import get_llm
from backend.app.services.neo4j_analytics.helpers import parse_json_from_response, _format_recent_messages
from app.services.agents.ontology_loader import get_ontology_summary
from app.services.agents.prompts import DOMAIN_SPECIALIST_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State


async def domain_specialist_node(state: State) -> dict:
    question = state.get("question", "")
    persona = state.get("persona", "Analyst")
    complexity = state.get("complexity", "simple")
    summary = state.get("summary", "")
    messages = state.get("messages", [])
    t0 = time.perf_counter()

    recent = _format_recent_messages(messages, n=4)
    conversation_context = "\n\n".join(filter(None, [summary, recent])) or "None."

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    chain = DOMAIN_SPECIALIST_PROMPT | get_llm("balanced")
    raw = await chain.ainvoke({
        "question": question,
        "persona": persona,
        "complexity": complexity,
        "conversation_context": conversation_context,
        "reasoning_directive": reasoning_directive,
        "ontology_context": get_ontology_summary() or "Not available.",
    })
    text = raw.content if hasattr(raw, "content") else str(raw)
    parsed = parse_json_from_response(text)

    intent = parsed.get("intent", "balance_lookup")
    routing = parsed.get("routing", "kg_only")
    hil_required = bool(parsed.get("hil_required", False))

    # Force HIL for Executive + advanced complexity
    if persona == "Executive" and complexity == "advanced":
        hil_required = True

    step = {
        "node": "domain_specialist",
        "label": "Identifying data scope",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "intent": intent,
        "routing": routing,
        "hil_required": hil_required,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

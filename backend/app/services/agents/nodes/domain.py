"""domain_specialist_node — determine intent and routing for a kg_query."""

from __future__ import annotations

import time

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_json_from_response
from app.services.agents.prompts import DOMAIN_SPECIALIST_PROMPT
from app.services.agents.state import State


async def domain_specialist_node(state: State) -> dict:
    question = state.get("question", "")
    persona = state.get("persona", "Analyst-F")
    complexity = state.get("complexity", "simple")
    t0 = time.perf_counter()

    chain = DOMAIN_SPECIALIST_PROMPT | get_llm("balanced")
    raw = await chain.ainvoke({
        "question": question,
        "persona": persona,
        "complexity": complexity,
    })
    text = raw.content if hasattr(raw, "content") else str(raw)
    parsed = parse_json_from_response(text)

    intent = parsed.get("intent", "balance_lookup")
    routing = parsed.get("routing", "kg_only")
    hil_required = bool(parsed.get("hil_required", False))

    # Force HIL for Executive + advanced complexity
    if persona == "Executive-F" and complexity == "advanced":
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

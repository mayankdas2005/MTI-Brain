"""plan_node — decompose an Advanced question into a sub-question DAG."""

from __future__ import annotations

import json
import time

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_tag, _format_recent_messages
from app.services.agents.ontology_loader import get_ontology_summary
from app.services.agents.prompts import PLAN_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import State


def _parse_plan(text: str) -> dict:
    plan_raw = parse_tag(text, "plan")
    if not plan_raw:
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        plan_raw = m.group() if m else "{}"
    try:
        return json.loads(plan_raw)
    except (json.JSONDecodeError, ValueError):
        return {}


async def plan_node(state: State) -> dict:
    question = state.get("question", "")
    persona = state.get("persona", "Executive")
    plan_attempts = state.get("plan_attempts", 0)
    halt_reason = state.get("halt_reason", "")
    summary = state.get("summary", "")
    messages = state.get("messages", [])
    t0 = time.perf_counter()

    prior_context = (
        f"Note: prior decomposition failed — {halt_reason}. Re-decompose avoiding that issue."
        if plan_attempts > 0 and halt_reason else ""
    )

    recent = _format_recent_messages(messages, n=4)
    conversation_context = "\n\n".join(filter(None, [summary, recent])) or "None."

    tier = "deep" if state.get("deep_analysis") else "balanced"
    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL
    chain = PLAN_PROMPT | get_llm(tier)
    raw = await chain.ainvoke({
        "question": question,
        "persona": persona,
        "ontology_summary": get_ontology_summary(),
        "prior_context": prior_context,
        "conversation_context": conversation_context,
        "reasoning_directive": reasoning_directive,
    })
    text = raw.content if hasattr(raw, "content") else str(raw)
    plan = _parse_plan(text)

    nodes = plan.get("nodes", [])
    if not nodes:
        nodes = [{"id": "sq1", "question": question, "depends_on": [], "intent": "balance_lookup"}]
        plan = {"nodes": nodes, "edges": [], "budget": {"max_seconds": 45, "max_subqs": 8}}

    sub_questions = [
        {
            "id": n.get("id", f"sq{i+1}"),
            "question": n.get("question", ""),
            "depends_on": n.get("depends_on", []),
            "intent": n.get("intent", ""),
            "status": "pending",
            "bindings": {},
            "sparql": "",
            "error": "",
            "attempt": 0,
            "l1_count": 0,
            "l2_count": 0,
        }
        for i, n in enumerate(nodes)
    ]

    step = {
        "node": "plan",
        "label": f"Planning sub-questions ({len(nodes)} sub-Qs)",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "plan": plan,
        "sub_questions": sub_questions,
        "scratchpad": {},
        "plan_attempts": plan_attempts + 1,
        "halt_reason": None,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

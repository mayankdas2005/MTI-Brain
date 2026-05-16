"""sparql_gen_node — generate SPARQL using ontology context and tribal facts."""

from __future__ import annotations

import time

from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_sparql_from_response
from app.services.agents.ontology_loader import get_ontology_summary
from app.services.agents.prompts import SPARQL_GEN_PROMPT, SPARQL_FIX_PROMPT
from app.services.agents.state import State


def _format_ontology_terms(terms: list[dict]) -> str:
    if not terms:
        return "No specific terms resolved — use lpp: prefix with ontology reference."
    lines = []
    for t in terms:
        lines.append(f"  lpp:{t['local']} ({t['type']})")
    return "\n".join(lines)


def _format_tribal_facts(facts: list[dict]) -> str:
    if not facts:
        return "None."
    lines = []
    for f in facts[:10]:
        label = f.get("label", "?")
        value = f.get("value", "")
        ftype = f.get("type", "")
        lines.append(f"  [{ftype}] {label}" + (f": {value}" if value else ""))
    return "\n".join(lines)


async def sparql_gen_node(state: State) -> dict:
    question = state.get("question", "")
    intent = state.get("intent", "")
    persona = state.get("persona", "Analyst-F")
    ontology_terms = state.get("ontology_terms", [])
    tribal_facts = state.get("tribal_facts", [])
    sparql_error = state.get("sparql_error", "")
    sparql_retries = state.get("sparql_retries", 0)
    existing_sparql = state.get("sparql", "")
    t0 = time.perf_counter()

    tier = "deep" if sparql_retries >= 2 else "balanced"

    if sparql_error and existing_sparql:
        prompt = SPARQL_FIX_PROMPT
        chain = prompt | get_llm(tier)
        raw = await chain.ainvoke({
            "question": question,
            "intent": intent,
            "sparql": existing_sparql,
            "error": sparql_error,
            "ontology_summary": get_ontology_summary(),
        })
    else:
        prompt = SPARQL_GEN_PROMPT
        chain = prompt | get_llm(tier)
        raw = await chain.ainvoke({
            "question": question,
            "intent": intent,
            "persona": persona,
            "ontology_summary": get_ontology_summary(),
            "ontology_terms": _format_ontology_terms(ontology_terms),
            "tribal_facts": _format_tribal_facts(tribal_facts),
            "prior_error": sparql_error or "None.",
        })

    text = raw.content if hasattr(raw, "content") else str(raw)
    sparql = parse_sparql_from_response(text) or text.strip()

    step = {
        "node": "sparql_gen",
        "label": f"Generating SPARQL query" + (" (repair)" if sparql_error else ""),
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "tier": tier,
    }
    return {
        "sparql": sparql,
        "sparql_error": "",
        "sparql_retries": sparql_retries + 1 if sparql_error else sparql_retries,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

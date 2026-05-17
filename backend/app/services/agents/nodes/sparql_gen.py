"""sparql_gen_node — generate SPARQL using ontology context and tribal facts."""

from __future__ import annotations

import time

from app.core.logger import logger
from app.services.agents.bedrock import get_llm
from app.services.agents.helpers import parse_sparql_from_response
from app.services.agents.ontology_loader import get_ontology_summary
from app.services.agents.prompts import SPARQL_GEN_PROMPT, SPARQL_FIX_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
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
    persona = state.get("persona", "Analyst")
    ontology_terms = state.get("ontology_terms", [])
    tribal_facts = state.get("tribal_facts", [])
    sparql_error = state.get("sparql_error", "")
    sparql_retries = state.get("sparql_retries", 0)
    existing_sparql = state.get("sparql", "")
    prior_sql = state.get("prior_sql", "")
    max_rows = state.get("max_rows", 100)
    t0 = time.perf_counter()

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL

    # Build refinement context from prior_sql — only on the FIRST generation (no error yet)
    refinement_section = ""
    if prior_sql and not sparql_error:
        refinement_section = (
            "The user is refining a previous answer. "
            "Modify the SPARQL below to satisfy the user's instruction. "
            "Preserve the original query's structure and intent — only change what is needed.\n\n"
            f"Previous SPARQL to modify:\n```sparql\n{prior_sql}\n```"
        )

    if sparql_error and existing_sparql:
        prompt = SPARQL_FIX_PROMPT
        chain = prompt | get_llm("deep")
        raw = await chain.ainvoke({
            "question": question,
            "intent": intent,
            "sparql": existing_sparql,
            "error": sparql_error,
            "ontology_terms": _format_ontology_terms(ontology_terms),
            "feedback_context": state.get("feedback_context") or "None.",
            "reasoning_directive": reasoning_directive,
        })
    else:
        prompt = SPARQL_GEN_PROMPT
        chain = prompt | get_llm("deep")
        raw = await chain.ainvoke({
            "question": question,
            "intent": intent,
            "persona": persona,
            "ontology_summary": get_ontology_summary(),
            "ontology_terms": _format_ontology_terms(ontology_terms),
            "tribal_facts": _format_tribal_facts(tribal_facts),
            "prior_error_section": f"Prior error (fix this):\n{sparql_error}" if sparql_error else "",
            "refinement_section": refinement_section,
            "conversation_context": state.get("summary") or "None.",
            "cross_thread_context": state.get("cross_thread_context") or "None.",
            "feedback_context": state.get("feedback_context") or "None.",
            "max_rows": max_rows,
            "reasoning_directive": reasoning_directive,
        })

    text = raw.content if hasattr(raw, "content") else str(raw)
    logger.debug(
        f"[sparql_gen] has_reasoning={('<reasoning>' in text.lower())} "
        f"has_sparql={('<sparql>' in text.lower())} "
        f"preview={text[:400]!r}"
    )
    sparql = parse_sparql_from_response(text) or text.strip()

    step = {
        "node": "sparql_gen",
        "label": f"Generating SPARQL query" + (" (repair)" if sparql_error else ""),
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "tier": "deep",
    }
    return {
        "sparql": sparql,
        "sparql_error": "",
        "sparql_retries": sparql_retries + 1 if sparql_error else sparql_retries,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

"""brain_retrieval_node — retrieve Tribal graph facts (Policy, Limit, Decision)."""

from __future__ import annotations

import time

from app.services.agents import data_pool as dp
from app.services.agents.state import State


TRIBAL_CLASSES = [
    "tribal:Policy",
    "tribal:Limit",
    "tribal:Decision",
    "tribal:Commitment",
    "tribal:Watchlist",
    "tribal:HedgeIntent",
]

_TRIBAL_QUERY_TEMPLATE = """
PREFIX tribal: <https://lpp.example/tribal#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?type ?label ?value ?status ?effectiveFrom ?effectiveTo
WHERE {{
  ?fact a ?type ;
        rdfs:label ?label .
  OPTIONAL {{ ?fact tribal:value ?value }}
  OPTIONAL {{ ?fact tribal:status ?status }}
  OPTIONAL {{ ?fact tribal:effectiveFrom ?effectiveFrom }}
  OPTIONAL {{ ?fact tribal:effectiveTo ?effectiveTo }}
  FILTER(
    ?type IN ({classes})
    && (
      CONTAINS(LCASE(?label), "{keyword1}") ||
      CONTAINS(LCASE(?label), "{keyword2}")
    )
  )
  FILTER(!BOUND(?status) || ?status = "active")
}}
LIMIT 20
"""


def _extract_keywords(question: str, intent: str) -> tuple[str, str]:
    INTENT_KEYWORDS = {
        "counterparty_exposure": ("limit", "exposure"),
        "policy_check": ("policy", "limit"),
        "fx_exposure": ("hedge", "fx"),
        "investment_positions": ("investment", "limit"),
        "maturity_ladder": ("maturity", "limit"),
        "scenario_forecast": ("threshold", "limit"),
    }
    words = question.lower().split()
    kw1, kw2 = INTENT_KEYWORDS.get(intent, ("limit", "policy"))
    for w in words:
        if len(w) > 4 and w not in {"what", "show", "give", "list", "find", "that", "this", "with", "from"}:
            kw1 = w
            break
    return kw1, kw2


async def brain_retrieval_node(state: State) -> dict:
    routing = state.get("routing", "kg_only")
    t0 = time.perf_counter()

    if routing == "kg_only":
        return {
            "tribal_facts": [],
            "pipeline_steps": state.get("pipeline_steps", []) + [{
                "node": "brain_retrieval",
                "label": "Tribal graph skipped (KG-only routing)",
                "duration_ms": 0,
            }],
        }

    question = state.get("question", "")
    intent = state.get("intent", "")
    kw1, kw2 = _extract_keywords(question, intent)

    classes_str = ", ".join(TRIBAL_CLASSES)
    query = _TRIBAL_QUERY_TEMPLATE.format(
        classes=classes_str,
        keyword1=kw1,
        keyword2=kw2,
    )

    try:
        client = dp.get_tribal_client()
        columns, rows, raw_bindings = await client.execute_select(query)
        facts: list[dict] = []
        for binding in raw_bindings:
            fact = {k: v.get("value") for k, v in binding.items() if v}
            fact["type"] = fact.get("type", "").split("#")[-1]
            facts.append(fact)
    except Exception as e:
        from app.core.logger import logger
        logger.warning(f"Tribal graph retrieval failed: {e}")
        facts = []

    step = {
        "node": "brain_retrieval",
        "label": "Retrieving policy context",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "facts_found": len(facts),
    }
    return {
        "tribal_facts": facts,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

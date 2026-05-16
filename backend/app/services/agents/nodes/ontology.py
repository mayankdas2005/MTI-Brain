"""ontology_lookup_node — resolve question terms to lpp: URIs (no LLM)."""

from __future__ import annotations

import time

from app.services.agents.ontology_loader import resolve_term, get_ontology_dict
from app.services.agents.state import State


async def ontology_lookup_node(state: State) -> dict:
    question = state.get("question", "")
    intent = state.get("intent", "")
    t0 = time.perf_counter()

    ontology = get_ontology_dict()
    if not ontology:
        return {"ontology_terms": [], "pipeline_steps": state.get("pipeline_steps", [])}

    search_tokens = set((question + " " + intent).lower().split())

    NOISE = {"the", "a", "an", "is", "are", "for", "of", "in", "on", "at", "to", "by", "with", "all"}
    search_tokens -= NOISE

    matched: list[dict] = []
    seen_uris: set[str] = set()

    for token in search_tokens:
        if len(token) < 3:
            continue
        for term in resolve_term(token):
            uri = term.get("uri", "")
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                matched.append({
                    "uri": uri,
                    "local": term.get("local", ""),
                    "label": term.get("label", ""),
                    "type": term.get("type", ""),
                    "property_type": term.get("range", None),
                })

    # Always include the most relevant core classes based on intent
    INTENT_CLASSES = {
        "balance_lookup": ["BankAccount", "Company"],
        "counterparty_exposure": ["Bank", "BankAccount", "InvestmentPosition", "FxForward", "DerivativeMtm"],
        "fx_exposure": ["FxForward", "Company", "Bank"],
        "investment_positions": ["InvestmentPosition", "InvestmentInstrument", "Company"],
        "maturity_ladder": ["InvestmentPosition", "FxForward"],
        "policy_check": ["BankAccount", "Company", "Bank"],
        "code_lookup": ["Bank", "Company", "BankAccount"],
        "trend_analysis": ["InvestmentPosition", "BankAccount"],
        "scenario_forecast": ["InvestmentPosition", "FxForward"],
        "multi_entity_join": ["Company", "Bank", "BankAccount", "InvestmentPosition"],
    }

    hint_classes = INTENT_CLASSES.get(intent, [])
    LPP_NS = "https://lpp.example/ontology#"
    for cls_name in hint_classes:
        uri = f"{LPP_NS}{cls_name}"
        if uri not in seen_uris:
            seen_uris.add(uri)
            matched.append({"uri": uri, "local": cls_name, "label": cls_name, "type": "class", "property_type": None})

    step = {
        "node": "ontology_lookup",
        "label": "Resolving ontology terms",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "ontology_terms": matched,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

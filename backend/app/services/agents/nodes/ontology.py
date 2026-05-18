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
        # Treasury
        "balance_lookup":           ["BankAccount", "BalanceSnapshot", "CashPosition", "Currency", "Company"],
        "exposure_analysis":        ["Bank", "BankAccount", "InvestmentPosition", "FxForward", "DerivativeMtm", "CounterpartyExposure", "Company"],
        "investment_and_maturity":  ["InvestmentPosition", "FinancialInstrument", "Company", "Bank"],
        # Payments
        "authorization_analysis":   ["Authorization", "MerchantAccount", "Acquirer", "CardNetwork", "CardType", "Transaction"],
        "cost_and_fee_analysis":    ["BankFee", "Chargeback", "Settlement", "CardPaymentRollup", "Transaction", "FraudLossEvent"],
        "payment_operations":       ["Transaction", "PaymentHubEvent", "PaymentBatch", "StpMetric", "Settlement", "PaymentFile"],
        "supplier_and_crossborder": ["Invoice", "Counterparty", "FxForward", "WorkingCapitalMetric", "Company"],
        # Strategic
        "trend_and_forecast":       ["BankAccount", "BalanceSnapshot", "InvestmentPosition", "CashForecast", "ForecastLine", "FxForward"],
        "code_lookup":              ["Bank", "Company", "BankAccount"],
        "general_analytics":        ["BankAccount", "BalanceSnapshot", "CashPosition", "Company", "Bank"],
        # Legacy keys (kept for backward compatibility)
        "counterparty_exposure":    ["Bank", "BankAccount", "InvestmentPosition", "FxForward", "DerivativeMtm", "CounterpartyExposure"],
        "fx_exposure":              ["FxForward", "FxExposure", "Company", "Bank"],
        "investment_positions":     ["InvestmentPosition", "FinancialInstrument", "Company"],
        "maturity_ladder":          ["InvestmentPosition", "FxForward"],
        "policy_check":             ["BankAccount", "Company", "Bank"],
        "trend_analysis":           ["InvestmentPosition", "BankAccount", "BalanceSnapshot"],
        "scenario_forecast":        ["InvestmentPosition", "FxForward", "CashForecast", "Scenario", "StressTest"],
        "multi_entity_join":        ["Company", "Bank", "BankAccount", "InvestmentPosition"],
    }

    hint_classes = INTENT_CLASSES.get(intent, [])
    LPP_NS = "https://lpp.example/ontology#"
    # Only inject classes that actually exist in the loaded ontology
    known_uris = set(ontology.keys())
    for cls_name in hint_classes:
        uri = f"{LPP_NS}{cls_name}"
        if uri not in seen_uris and uri in known_uris:
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

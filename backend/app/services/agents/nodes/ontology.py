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
        # Payments — concrete PaymentTransaction subclasses (not the abstract Transaction base)
        "authorization_analysis":   ["CardTransaction", "VirtualCardTransaction", "CommercialCardTransaction", "Authorization", "MerchantAccount", "Acquirer", "CardNetwork", "CardType"],
        "cost_and_fee_analysis":    ["WireTransfer", "AchTransaction", "CardTransaction", "BankFee", "Chargeback", "Settlement", "CardPaymentRollup", "FraudLossEvent"],
        "payment_operations":       ["WireTransfer", "AchTransaction", "RtpTransaction", "FedNowTransaction", "CheckPayment", "CrossBorderPayment", "CardTransaction", "PaymentHubEvent", "PaymentBatch", "StpMetric", "Settlement", "PaymentFile"],
        "supplier_and_crossborder": ["Invoice", "Counterparty", "FxForward", "WorkingCapitalMetric", "Company", "CrossBorderPayment"],
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
    # Build a set of class URIs that actually exist in the loaded ontology.
    # NOTE: ontology.keys() returns top-level dict keys ('classes', 'object_properties', …),
    # not class URIs — always index into ontology["classes"] to get real URIs.
    known_class_uris = {c["uri"] for c in ontology.get("classes", [])}
    known_class_by_local = {c["local"]: c for c in ontology.get("classes", [])}
    for cls_name in hint_classes:
        uri = f"{LPP_NS}{cls_name}"
        if uri not in seen_uris and uri in known_class_uris:
            cls_entry = known_class_by_local.get(cls_name, {})
            seen_uris.add(uri)
            matched.append({
                "uri": uri,
                "local": cls_name,
                "label": cls_entry.get("label", cls_name),
                "comment": cls_entry.get("comment", ""),
                "type": "class",
                "property_type": None,
            })

    step = {
        "node": "ontology_lookup",
        "label": "Resolving ontology terms",
        "duration_ms": round((time.perf_counter() - t0) * 1000),
    }
    return {
        "ontology_terms": matched,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }

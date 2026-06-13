"""Node 0: intake_classifier — routes general_chat vs analytics.

Three-layer classification:
  Layer 1: Fast pre-filter (no LLM) — affirmatives use state signal; obvious greetings caught by pattern
  Layer 2: LLM classifier (Haiku) with domain/intent context from Neo4j graph
  Layer 3: Error fallback → analytics (better to attempt SQL than silently drop a data question)
"""

from __future__ import annotations

import asyncio
import json_repair as _json
import re as _re
import json

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import INTAKE_CLASSIFY_PROMPT, INTAKE_INTENT_PROMPT, INTAKE_SEARCH_PROMPT
from app.services.agents.state import AnalyticsState


# ── Layer 1: fast pre-filter patterns ─────────────────────────────────────────

_AFFIRMATIVES = frozenset({
    "yes", "yep", "yeah", "sure", "ok", "okay", "alright",
    "go ahead", "do it", "proceed", "continue",
    "show me", "show that", "please", "absolutely", "of course",
})

_GREETINGS = frozenset({
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "good night", "greetings",
    "thanks", "thank you", "ty", "thx",
    "bye", "goodbye", "see you", "later",
    "cool", "got it", "understood", "noted", "great", "nice",
})

_CAPABILITY_RE = _re.compile(
    r"(what can you|what do you do|how do you work|what are you"
    r"|who are you|introduce yourself|tell me about yourself"
    r"|help\s+(me\s+)?understand (what|how))",
    _re.IGNORECASE,
)


# ── Lazy-loaded Neo4j context (populated on first classifier call) ─────────────

_CLASSIFIER_CONTEXT: dict = {}

_REDIS_CTX_KEY = "classifier:context:v1"
_REDIS_CTX_TTL = 86400  # 1 Day

_FALLBACK_DOMAINS = (
    "banking, cash_and_liquidity, payments, fx_and_hedging, "
    "debt_and_capital, investments, fraud"
)
_FALLBACK_INTENTS = (
    "balance_lookup, trend_analysis, scenario_forecast, "
    "payment_operations, policy_check, counterparty_exposure, "
    "exposure_analysis, maturity_ladder"
)


def _get_classifier_context() -> dict:
    """Load domain + intent context — three tiers: in-process → Redis → Neo4j.

    L1 (in-process): ~0ms, survives for process lifetime after first load.
    L2 (Redis): ~1ms, survives restarts and is shared across workers, TTL 1 day.
    L3 (Neo4j): authoritative source; result written to both Redis and in-process.
    Falls back to hardcoded defaults if all tiers fail.
    """
    global _CLASSIFIER_CONTEXT
    if _CLASSIFIER_CONTEXT:
        return _CLASSIFIER_CONTEXT

    try:
        from app.services.agents import redis_client as _rc
        raw = _rc.cache_get(_REDIS_CTX_KEY)
        if raw:
            _CLASSIFIER_CONTEXT = _json.loads(raw)
            logger.debug("intake_classifier | context | redis_hit")
            return _CLASSIFIER_CONTEXT
    except Exception:
        pass

    try:
        from app.services.agents import neo4j_client as nc
        domain_names = nc.get_all_domain_names()
        intent_names = nc.get_all_intent_names()
        _CLASSIFIER_CONTEXT = {
            "domain_list": ", ".join(domain_names) if domain_names else _FALLBACK_DOMAINS,
            "intent_list": "\n  ".join(intent_names) if intent_names else _FALLBACK_INTENTS,
        }
        try:
            from app.services.agents import redis_client as _rc
            _rc.cache_set(_REDIS_CTX_KEY, json.dumps(_CLASSIFIER_CONTEXT), ttl_seconds=_REDIS_CTX_TTL)
        except Exception:
            pass
        logger.info(
            "intake_classifier | context loaded | domains={} | intents={}",
            len(domain_names), len(intent_names),
        )
    except Exception as e:
        logger.warning("intake_classifier | context load failed (using defaults) | error={}", e)
        _CLASSIFIER_CONTEXT = {
            "domain_list": _FALLBACK_DOMAINS,
            "intent_list": _FALLBACK_INTENTS,
        }
    return _CLASSIFIER_CONTEXT


async def warmup_classifier_context() -> None:
    """Pre-populate the in-process + Redis context cache from Neo4j.

    Called once at startup so the first real query hits L1 (in-process) cache.
    """
    ctx = await asyncio.to_thread(_get_classifier_context)
    logger.info(
        "warmup | intake_classifier context ready | domains={} | intents={}",
        len((ctx.get("domain_list") or "").split(",")),
        len((ctx.get("intent_list") or "").split("\n")),
    )


# ── State-derived prior-turn signal ───────────────────────────────────────────

def _prior_was_analytics(state: AnalyticsState) -> bool:
    """True if prior turn went through the analytics pipeline — success, no-data, or error.

    Uses question_type alone. A failed SQL (execution_error set, result_list=[]) still
    means the user is in an analytics conversation — 'yes' after a broken query means
    'yes, try again', not 'yes, great chat response'.
    """
    return state.get("question_type") == "analytics"


# ── Layer 1: fast pre-filter ───────────────────────────────────────────────────

def _quick_classify(question: str, prior_analytics: bool) -> str | None:
    """Return classification for unambiguous cases without any LLM call.

    Returns None when the question is ambiguous and needs the LLM classifier.
    """
    q = question.strip().lower().rstrip("?.!")

    # Short affirmatives — route based on state signal (no LLM needed)
    if q in _AFFIRMATIVES:
        result = "analytics" if prior_analytics else "general_chat"
        logger.debug("intake_classifier | fast_affirmative | '{}' → {} | prior_analytics={}", q, result, prior_analytics)
        return result

    # Greetings — single word
    if q in _GREETINGS:
        return "general_chat"

    # Multi-word greetings (e.g., "good morning", "thank you very much")
    words = q.split()
    if len(words) <= 4 and all(w in _GREETINGS for w in words):
        return "general_chat"

    # Explicit capability questions
    if _CAPABILITY_RE.search(question):
        return "general_chat"

    return None  # Needs LLM


# ── Main node ──────────────────────────────────────────────────────────────────

async def intake_classifier(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info(
        "intake_classifier START | thread={} | question={}",
        state["thread_id"], state["question"][:80],
    )

    question = state["question"]
    prior_analytics = _prior_was_analytics(state)

    # Layer 1: fast pre-filter (no LLM call)
    quick_result = _quick_classify(question, prior_analytics)
    if quick_result:
        logger.info(
            "intake_classifier | fast_path | type={} | thread={}",
            quick_result, state["thread_id"],
        )
        return {"question_type": quick_result, "specialist_outputs": [{"__reset__": True}]}

    # Layer 2: P1 — 3 focused LLM calls instead of 1 monolithic call
    ctx = _get_classifier_context()
    conversation_context = _format_conversation(
        state.get("messages", []), state.get("summary") or ""
    )

    # Call A: classification + decision_type (fast — Haiku, ~150 token prompt)
    prompt_a = INTAKE_CLASSIFY_PROMPT.format_messages(
        question=question,
        conversation_context=conversation_context,
        domain_list=ctx["domain_list"],
    )
    raw_a = await _call_llm(prompt_a, config)
    question_type, is_followup, complexity, decision_type, has_reconciliation = _parse_classify(raw_a)

    if question_type == "general_chat":
        logger.info(
            "intake_classifier DONE | thread={} | type=general_chat | fast_exit",
            state["thread_id"],
        )
        return {
            "question_type": "general_chat",
            "is_followup": is_followup,
            "complexity": complexity,
            "decision_type": decision_type,
            "has_reconciliation": has_reconciliation,
            "has_multi_domain": False,
            "entity_tokens": None,
            "search_terms": None,
            "search_variants": None,
            "query_intent": None,
            "specialist_outputs": [{"__reset__": True}],
        }

    # Calls B + C in parallel (both only needed for analytics)
    prompt_b = INTAKE_INTENT_PROMPT.format_messages(
        question=question,
        decision_type=decision_type,
    )
    prompt_c = INTAKE_SEARCH_PROMPT.format_messages(
        question=question,
        complexity=complexity,
    )
    raw_b, raw_c = await asyncio.gather(
        _call_llm(prompt_b, config),
        _call_llm(prompt_c, config),
    )

    query_intent = _parse_intent(raw_b)
    entity_tokens, search_terms, search_variants = _parse_search(raw_c)

    # Derive has_multi_domain deterministically from DOMAIN line count (not LLM output)
    has_multi_domain = len([l for l in query_intent if l.startswith("DOMAIN:")]) >= 3

    # Combine current search_terms with prior turn's terms for follow-up queries
    prior_search_terms = list(state.get("search_terms") or [])
    combined_search_terms = _combine_search_terms(search_terms, prior_search_terms, is_followup)

    logger.info(
        "intake_classifier DONE | thread={} | type={} | is_followup={} | complexity={} | decision_type={} | has_reconciliation={} | has_multi_domain={} | entity_tokens={} | search_terms={} | combined={} | prior_analytics={} | query_intent={}",
        state["thread_id"], question_type, is_followup, complexity, decision_type, has_reconciliation, has_multi_domain,
        entity_tokens, search_terms, combined_search_terms, prior_analytics, query_intent,
    )
    return {
        "question_type": question_type,
        "is_followup": is_followup,
        "complexity": complexity,
        "decision_type": decision_type,
        "has_reconciliation": has_reconciliation,
        "has_multi_domain": has_multi_domain,
        "entity_tokens": entity_tokens or None,
        "search_terms": combined_search_terms or None,
        "search_variants": search_variants or entity_tokens or None,
        "query_intent": query_intent or None,
        "specialist_outputs": [{"__reset__": True}],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _call_llm(prompt, config: RunnableConfig) -> str:
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    merged_config = dict(config)
    merged_config["tags"] = list(merged_config.get("tags", [])) + ["no_stream"]

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=merged_config), service="bedrock-intake-classifier", max_attempts=2, backoff_base=5.0)

    response = await _call()
    return response.content or ""


_VALID_DECISION_TYPES = frozenset({"lookup", "breach_detection", "trend_analysis", "comparison", "judgment", "multi_domain"})

_CANONICAL_DOMAINS = {
    "cash_and_liquidity": ["cash", "liquidity", "balance", "sweep", "intercompany"],
    "benchmarking": ["benchmark", "sofr", "sonia", "rate index", "interest rate"],
    "debt_and_capital": ["debt", "credit", "facility", "borrowing", "capital"],
    "fx_and_hedging": ["fx", "foreign exchange", "hedge", "forward", "derivative"],
    "forecasting": ["forecast", "projection", "variance"],
    "fraud": ["fraud", "risk score", "chargeback"],
    "erp_reconciliation": ["reconciliation", "gl", "general ledger", "close"],
    "investments": ["investment", "portfolio", "deposit", "bond"],
    "reference": ["currency", "counterparty", "master data"],
    "knowledge_graph": ["institutional", "tribal", "sme"],
}


def _normalize_domain_name(raw: str) -> str:
    raw_lower = raw.lower().strip()
    if raw_lower in _CANONICAL_DOMAINS:
        return raw_lower
    for canonical, keywords in _CANONICAL_DOMAINS.items():
        if any(kw in raw_lower for kw in keywords):
            return canonical
    return raw_lower


def _parse_intake(raw: str) -> tuple[str, bool, str, list[str], list[str], list[str], list[str], str, bool]:
    """Legacy single-call parser — kept for backward compat with any callers."""
    from json_repair import loads as json_loads
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        qtype = data.get("type", "analytics")
        if qtype not in ("general_chat", "analytics"):
            qtype = "analytics"
        is_followup     = bool(data.get("is_followup", False))
        complexity      = data.get("complexity", "simple")
        if complexity not in ("simple", "complex", "advanced"):
            complexity = "simple"
        decision_type = data.get("decision_type", "lookup")
        if decision_type not in _VALID_DECISION_TYPES:
            decision_type = "lookup"
        has_reconciliation = bool(data.get("has_reconciliation", False))
        entity_tokens   = [str(e) for e in (data.get("entity_tokens") or []) if e][:8]
        search_terms    = [str(t) for t in (data.get("search_terms") or []) if t][:6]
        search_variants = [str(v) for v in (data.get("search_variants") or []) if v][:8]
        _VALID_LABELS = ("GOAL:", "TIME:", "COMPARISON:", "DOMAIN:", "CONDITION:", "SCENARIO:", "CONTEXT:", "OUTPUT:")
        raw_intent = data.get("query_intent") or []
        query_intent = []
        for line in raw_intent:
            line_s = str(line).strip()
            if not line_s or not any(line_s.startswith(lbl) for lbl in _VALID_LABELS):
                continue
            if line_s.startswith("DOMAIN:"):
                raw_domain = line_s[len("DOMAIN:"):].strip()
                normalized = _normalize_domain_name(raw_domain)
                line_s = f"DOMAIN: {normalized}"
            query_intent.append(line_s)
            if len(query_intent) >= 12:
                break
        return qtype, is_followup, complexity, entity_tokens, search_terms, search_variants, query_intent, decision_type, has_reconciliation
    except Exception:
        return "analytics", False, "simple", [], [], [], [], "lookup", False


def _parse_classify(raw: str) -> tuple[str, bool, str, str, bool]:
    """Parse Call A output: (question_type, is_followup, complexity, decision_type, has_reconciliation)."""
    from json_repair import loads as json_loads
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        qtype = data.get("type", "analytics")
        if qtype not in ("general_chat", "analytics"):
            qtype = "analytics"
        is_followup = bool(data.get("is_followup", False))
        complexity = data.get("complexity", "simple")
        if complexity not in ("simple", "complex", "advanced"):
            complexity = "simple"
        decision_type = data.get("decision_type", "lookup")
        if decision_type not in _VALID_DECISION_TYPES:
            decision_type = "lookup"
        has_reconciliation = bool(data.get("has_reconciliation", False))
        return qtype, is_followup, complexity, decision_type, has_reconciliation
    except Exception:
        return "analytics", False, "simple", "lookup", False


def _parse_intent(raw: str) -> list[str]:
    """Parse Call B output: query_intent list of typed lines."""
    from json_repair import loads as json_loads
    _VALID_LABELS = ("GOAL:", "TIME:", "COMPARISON:", "DOMAIN:", "CONDITION:", "SCENARIO:", "CONTEXT:", "OUTPUT:")
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        raw_intent = data.get("query_intent") or []
        query_intent: list[str] = []
        for line in raw_intent:
            line_s = str(line).strip()
            if not line_s or not any(line_s.startswith(lbl) for lbl in _VALID_LABELS):
                continue
            if line_s.startswith("DOMAIN:"):
                raw_domain = line_s[len("DOMAIN:"):].strip()
                normalized = _normalize_domain_name(raw_domain)
                line_s = f"DOMAIN: {normalized}"
            query_intent.append(line_s)
            if len(query_intent) >= 12:
                break
        return query_intent
    except Exception:
        return []


def _parse_search(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Parse Call C output: (entity_tokens, search_terms, search_variants)."""
    from json_repair import loads as json_loads
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        entity_tokens   = [str(e) for e in (data.get("entity_tokens") or []) if e][:8]
        search_terms    = [str(t) for t in (data.get("search_terms") or []) if t][:6]
        search_variants = [str(v) for v in (data.get("search_variants") or []) if v][:8]
        return entity_tokens, search_terms, search_variants
    except Exception:
        return [], [], []


def _combine_search_terms(
    current_terms: list[str],
    prior_terms: list[str],
    is_followup: bool,
) -> list[str]:
    """For follow-up queries: merge current (priority) + prior terms, max 6, deduplicated by containment."""
    if not is_followup:
        return current_terms
    combined = list(current_terms)
    for pt in (prior_terms or []):
        if not any(pt.lower() in ct.lower() or ct.lower() in pt.lower() for ct in combined):
            combined.append(pt)
        if len(combined) >= 6:
            break
    return combined


def _format_conversation(messages: list, session_summary: str = "") -> str:
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-3:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:200]
        lines.append(f"{role}: {content}")
    if not lines and session_summary:
        return f"(prior conversation summary): {session_summary[:400]}"
    return "\n".join(lines) if lines else "(no prior context)"

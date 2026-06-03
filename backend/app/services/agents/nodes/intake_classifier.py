"""Node 0: intake_classifier — routes general_chat vs analytics.

Three-layer classification:
  Layer 1: Fast pre-filter (no LLM) — affirmatives use state signal; obvious greetings caught by pattern
  Layer 2: LLM classifier (Haiku) with domain/intent context from Neo4j graph
  Layer 3: Error fallback → analytics (better to attempt SQL than silently drop a data question)
"""

from __future__ import annotations

import asyncio
import json as _json
import re as _re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.agents.prompts import INTAKE_CLASSIFY_PROMPT
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
_REDIS_CTX_TTL = 3600  # 1 hour

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
    L2 (Redis): ~1ms, survives restarts and is shared across workers, TTL 1h.
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
            _rc.cache_set(_REDIS_CTX_KEY, _json.dumps(_CLASSIFIER_CONTEXT), ttl_seconds=_REDIS_CTX_TTL)
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
        return {"question_type": quick_result}

    # Layer 2: LLM classifier with domain/intent context from Neo4j
    ctx = _get_classifier_context()
    conversation_context = _format_conversation(
        state.get("messages", []), state.get("summary") or ""
    )

    prompt = INTAKE_CLASSIFY_PROMPT.format_messages(
        question=question,
        conversation_context=conversation_context,
        domain_list=ctx["domain_list"],
        intent_list=ctx["intent_list"],
        prior_was_analytics="YES" if prior_analytics else "NO",
    )

    result = await _call_llm(prompt, config)
    question_type = _parse_type(result)

    logger.info(
        "intake_classifier DONE | thread={} | type={} | prior_analytics={}",
        state["thread_id"], question_type, prior_analytics,
    )
    return {"question_type": question_type}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _call_llm(prompt, config: RunnableConfig) -> str:
    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    merged_config = dict(config)
    merged_config["tags"] = list(merged_config.get("tags", [])) + ["no_stream"]

    @llm_breaker
    async def _call():
        return await llm.ainvoke(prompt, config=merged_config)

    response = await _call()
    return response.content or ""


def _parse_type(raw: str) -> str:
    from json_repair import loads as json_loads
    try:
        output = parse_tag(raw, "output") or raw.strip()
        data = json_loads(output)
        qtype = data.get("type", "analytics")
        if qtype not in ("general_chat", "analytics"):
            return "analytics"
        return qtype
    except Exception:
        return "analytics"  # Layer 3 fallback: attempt analytics over silent drop


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

"""Node 0: intake_classifier — routes general_chat vs analytics.

Three-layer classification:
  Layer 1: Fast pre-filter (no LLM) — affirmatives use state signal; obvious greetings caught by pattern
  Layer 2: LLM classifier (Haiku) with domain/intent context from Neo4j graph
  Layer 3: Error fallback -> analytics (better to attempt SQL than silently drop a data question)
"""

from __future__ import annotations

import asyncio
import json_repair as _json
import re as _re
import json

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
    """Load domain + intent context — three tiers: in-process -> Redis -> Neo4j.

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
        logger.debug("intake_classifier | fast_affirmative | '{}' -> {} | prior_analytics={}", q, result, prior_analytics)
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

    from app.services.agents.nodes.lt_memory_retriever import lt_memory_retriever

    question = state["question"]
    prior_analytics = _prior_was_analytics(state)

    # Layer 1: fast pre-filter (no LLM call)
    quick_result = _quick_classify(question, prior_analytics)
    if quick_result:
        logger.info(
            "intake_classifier | fast_path | type={} | thread={}",
            quick_result, state["thread_id"],
        )
        memory_output = await lt_memory_retriever(state, config)
        classification_line = f"Classified as **{quick_result}** (fast path)"
        preference_label = _build_combined_label(memory_output, classification_line)
        return {
            "question_type": quick_result,
            "specialist_outputs": [{"__reset__": True}],
            **_extract_memory_keys(memory_output),
            "preference_label": preference_label,
        }

    # Layer 2: LLM classifier with domain/intent context from Neo4j
    # Run feedback retrieval concurrently with LLM classification
    memory_task = asyncio.create_task(lt_memory_retriever(state, config))

    ctx = _get_classifier_context()
    conversation_context = state.get("conversation_history") or "(no prior context)"

    prompt = INTAKE_CLASSIFY_PROMPT.format_messages(
        question=question,
        conversation_context=conversation_context,
        domain_list=ctx["domain_list"],
        intent_list=ctx["intent_list"],
        prior_was_analytics="YES" if prior_analytics else "NO",
    )

    result = await _call_llm(prompt, config)
    question_type, is_followup, complexity, entity_tokens, search_terms, search_variants, query_intent = _parse_intake(result)

    # Backstop: a genuine data-dependent follow-up must never resolve to general_chat,
    # regardless of how the LLM classified surface phrasing (e.g. "explain"/"why").
    if is_followup and prior_analytics and question_type == "general_chat":
        logger.info(
            "intake_classifier | followup_override | general_chat -> analytics | thread={}",
            state["thread_id"],
        )
        question_type = "analytics"

    # Derive query_type from structured query_intent signals
    query_type = _derive_query_type(query_intent)

    # Combine current search_terms with the IMMEDIATELY PRECEDING turn's own terms only.
    # Read from `_own_search_terms` (that turn's fresh, pre-merge terms), never from
    # `search_terms` (which is itself already a merged/accumulated value) — otherwise stale
    # terms from many turns ago ride along indefinitely across an entire thread, since each
    # turn's merge output becomes the next turn's "prior" input with no decay or turn-scoping.
    prior_search_terms = list(state.get("_own_search_terms") or [])
    combined_search_terms = _combine_search_terms(search_terms, prior_search_terms, is_followup)

    # Await feedback retrieval
    memory_output = await memory_task

    classification_line = f"Classified as **{question_type}** — {complexity}"
    if is_followup:
        classification_line += " (follow-up)"
    preference_label = _build_combined_label(memory_output, classification_line)

    logger.info(
        "intake_classifier DONE | thread={} | type={} | is_followup={} | complexity={} | entity_tokens={} | search_terms={} | combined={} | prior_analytics={} | query_intent_lines={} | query_intent={} | query_type={}",
        state["thread_id"], question_type, is_followup, complexity, entity_tokens,
        search_terms, combined_search_terms, prior_analytics, len(query_intent), query_intent, query_type,
    )
    return {
        "question_type": question_type,
        "is_followup": is_followup,
        "complexity": complexity,
        "query_type": query_type,
        "entity_tokens": entity_tokens or None,
        "search_terms": combined_search_terms or None,
        "_own_search_terms": search_terms or None,
        "search_variants": search_variants or entity_tokens or None,
        "query_intent": query_intent or None,
        "specialist_outputs": [{"__reset__": True}],
        **_extract_memory_keys(memory_output),
        "preference_label": preference_label,
    }


# ── Memory + label helpers ────────────────────────────────────────────────────

def _extract_memory_keys(memory_output: dict) -> dict:
    """Extract state keys produced by lt_memory_retriever."""
    return {
        "lt_memory_context":  memory_output.get("lt_memory_context", ""),
        "feedback_context":   memory_output.get("feedback_context", []),
        "preference_summary": memory_output.get("preference_summary"),
    }


def _build_combined_label(memory_output: dict, classification_line: str) -> str:
    """Combine feedback markdown and classification reasoning into one label."""
    feedback_label = memory_output.get("preference_label") or ""
    parts = []
    if feedback_label:
        parts.append(feedback_label)
    parts.append(f"\n{classification_line}")
    return "\n".join(parts)


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


def _parse_intake(raw: str) -> tuple[str, bool, str, list[str], list[str], list[str], list[str]]:
    """Parse LLM output — returns (question_type, is_followup, complexity, entity_tokens, search_terms, search_variants, query_intent)."""
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
        entity_tokens   = [str(e) for e in (data.get("entity_tokens") or []) if e][:8]
        search_terms    = [str(t) for t in (data.get("search_terms") or []) if t][:6]
        search_variants = [str(v) for v in (data.get("search_variants") or []) if v][:8]
        # query_intent: typed line list — validate each entry is a non-empty string starting with a known label
        _VALID_LABELS = ("GOAL:", "TIME:", "COMPARISON:", "DOMAIN:", "CONDITION:", "SCENARIO:", "CONTEXT:", "OUTPUT:")
        raw_intent = data.get("query_intent") or []
        query_intent = [
            str(line).strip()
            for line in raw_intent
            if str(line).strip() and any(str(line).strip().startswith(lbl) for lbl in _VALID_LABELS)
        ][:12]
        return qtype, is_followup, complexity, entity_tokens, search_terms, search_variants, query_intent
    except Exception as _e:
        logger.warning("intake_classifier | parse_failed | error={} | raw={}", _e, (raw or "")[:300])
        return "analytics", False, "simple", [], [], [], []  # Layer 3 fallback


def _derive_query_type(query_intent: list[str]) -> str | None:
    """Derive query_type from structured query_intent signals.

    Returns: lookup | aggregate | trend | comparison | ratio | None
    """
    if not query_intent:
        return None
    intent_text = " ".join(query_intent).lower()
    # TIME_INPUT signals projection/trend
    if any("TIME_INPUT" in line for line in query_intent):
        return "trend"
    # COMPUTATION with trend/OLS/slope keywords
    for line in query_intent:
        if line.startswith("GOAL:") or line.startswith("OUTPUT:"):
            ll = line.lower()
            if any(w in ll for w in ("trend", "slope", "ols", "projection", "forecast", "extrapolat")):
                return "trend"
            if any(w in ll for w in ("ratio", "rate", "spread", "percentage of")):
                return "ratio"
    # COMPARISON line present
    if any(line.startswith("COMPARISON:") for line in query_intent):
        return "comparison"
    # Default: aggregate (most common analytics query)
    return "aggregate"


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

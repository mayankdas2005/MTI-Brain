"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. Embeds the question via Cohere, searches Neo4j for templates/tables/columns,
fetches business terms and intents, applies community scoping, budget trims,
and injects short/long-term memory context.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import hashlib
import json
import time

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.memory import long_term, short_term
from app.services.neo4j_analytics.state import AnalyticsState

import re as _re

# Patterns that signal a follow-up response with no independent semantic content.
# The user is continuing a prior thread rather than asking a new question.
_FOLLOWUP_SIGNALS = _re.compile(
    r"^("
    # pure affirmatives
    r"yes|yep|yup|yeah|sure|ok|okay|alright|absolutely|of course|definitely|"
    r"sounds good|great|perfect|proceed|continue|go ahead|do it|please|"
    # continuation phrases
    r"show me|show that|more details?|more info|tell me more|elaborate|"
    r"break (it|that|this|them) down|drill (down|into)|"
    r"what about|and (also|then|what)|"
    # dangling pronouns — reference prior context with no standalone meaning
    r"(show|give|get|fetch|pull|display|list|what('s| is| are)) (me )?(the |those |that |it|them|more)"
    r")[\s.!?]*$",
    _re.IGNORECASE,
)

# If the question is this many words or fewer AND there is an active session
# summary, treat it as a contextual follow-up even if it doesn't match the
# patterns above (catches things like "by bank", "last month", "Q1 only").
_SHORT_FOLLOWUP_WORD_LIMIT = 5


def _is_followup_question(question: str, has_session_context: bool) -> bool:
    """Return True when the question cannot stand alone semantically.

    Three signals:
    1. Matches a known continuation pattern (affirmative, dangling pronoun, etc.)
    2. Very short question (≤ 5 words) with active session context
    3. Pure punctuation / single character — always a follow-up
    """
    stripped = question.strip()
    if not stripped or len(stripped) <= 2:
        return True
    if _FOLLOWUP_SIGNALS.match(stripped):
        return True
    if has_session_context:
        word_count = len(stripped.split())
        if word_count <= _SHORT_FOLLOWUP_WORD_LIMIT:
            return True
    return False


def _reconstruct_question(question: str, session_summary: str, previous_follow_ups: list[str]) -> str:
    """Build a semantically useful search query when the raw question is a follow-up.

    Priority order:
    1. First suggested follow-up from Q1 synthesis — most specific (e.g. "Break down by bank?")
    2. Last 300 chars of session summary — contains recent intent and entity identifiers
    3. Raw question as fallback (better than nothing)
    """
    if previous_follow_ups:
        return previous_follow_ups[0]
    if session_summary and len(session_summary) > 20:
        return session_summary[-300:].strip()
    return question


_STRIP_PROPS = {
    "cohere_embedding", "source_hash", "version", "pagerank_score", "betweenness_score",
    "wcc_component_id", "leiden_gamma", "modularity_contribution", "enrichment_status",
    "description_model", "ordinal_position", "null_frac", "n_distinct",
    "same_name_col_count", "encoded_pct", "size_mb", "type_confidence",
}


async def context_fetcher(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("context_fetcher START | thread={} | question={}", state["thread_id"], state["question"][:80])

    try:
        # Load short-term memory first — needed for follow-up reconstruction.
        session_summary = short_term.get_session_summary(state["thread_id"])

        # Detect affirmative follow-ups ("yes", "sure", "go ahead") and reconstruct
        # the effective search query from session context before embedding.
        raw_question = state["question"]
        is_followup = _is_followup_question(raw_question, bool(session_summary))
        if is_followup:
            previous_follow_ups = state.get("follow_ups") or []
            search_query = _reconstruct_question(raw_question, session_summary or "", previous_follow_ups)
            logger.info(
                "context_fetcher | affirmative follow-up detected | reconstructed={}",
                search_query[:80],
            )
        else:
            search_query = raw_question

        embedding = await _get_embedding(search_query)

        templates = neo4j_client.search_query_templates(embedding)
        tables = neo4j_client.search_tables_fulltext(search_query)
        columns = neo4j_client.search_columns_fulltext(search_query)
        tokens = _tokenize_with_bigrams(search_query)
        business_terms = neo4j_client.lookup_business_terms(tokens)
        intents = neo4j_client.search_intents(embedding)

        tables = _apply_community_scoping(tables)
        templates = _trim_objects(templates)
        tables = _trim_objects(tables)
        columns = _trim_objects(columns)

        memory_context = await long_term.retrieve_user_memory(state["user_id"], search_query)

        semantic_context = {
            "templates": templates,
            "tables": tables,
            "columns": columns,
            "business_terms": business_terms,
            "intents": intents,
            "session_summary": session_summary,
            "memory_context": memory_context,
            # effective_question lets downstream nodes know what was actually searched
            "effective_question": search_query if is_followup else None,
            "is_followup": is_followup,
        }

        logger.info(
            "context_fetcher DONE | thread={} | is_followup={} | templates={} | tables={} | cols={}",
            state["thread_id"], is_followup, len(templates), len(tables), len(columns),
        )
        return {"semantic_context": semantic_context, "error": None}

    except Exception as e:
        logger.error("context_fetcher FAILED | thread={} | error={}", state["thread_id"], e)
        return {"error": "semantic_layer_unavailable", "semantic_context": None}


async def _get_embedding(text: str) -> list[float]:
    normalized = text.strip().lower()
    cached = redis_client.get_embedding(normalized)

    if cached:
        logger.debug("cohere embed | cache_hit=True | ms=0")
        return cached

    t0 = time.monotonic()
    from app.services.embeddings import embed_question
    embedding = await embed_question(normalized)
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.debug("cohere embed | cache_hit=False | ms={:.0f}", elapsed_ms)

    redis_client.set_embedding(normalized, embedding)
    return embedding


def _tokenize_with_bigrams(text: str) -> list[str]:
    """Generate unigrams and bigrams, plus underscore-joined bigrams for term matching."""
    import re
    words = re.findall(r"\b\w+\b", text.lower())
    tokens = list(words)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        tokens.append(bigram)
        tokens.append(f"{words[i]}_{words[i+1]}")
    return tokens


def _apply_community_scoping(tables: list[dict]) -> list[dict]:
    """Filter tables to those in the same communities as fulltext matches."""
    if not tables:
        return tables
    community_ids = {t.get("community_id") for t in tables if t.get("community_id")}
    if not community_ids:
        return tables
    scoped = [t for t in tables if t.get("community_id") in community_ids]
    return scoped if scoped else tables


def _trim_objects(objects: list[dict]) -> list[dict]:
    """Remove internal/embedding properties and truncate long text fields."""
    trimmed = []
    for obj in objects:
        cleaned = {k: v for k, v in obj.items() if k not in _STRIP_PROPS}
        if "description" in cleaned and isinstance(cleaned["description"], str):
            cleaned["description"] = cleaned["description"][:80]
        if "synonyms" in cleaned and isinstance(cleaned["synonyms"], list):
            cleaned["synonyms"] = cleaned["synonyms"][:3]
        if "sample_values" in cleaned and isinstance(cleaned["sample_values"], list):
            cleaned["sample_values"] = cleaned["sample_values"][:5]
        trimmed.append(cleaned)
    return trimmed

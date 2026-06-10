"""Shared helpers for the context fetcher."""

from __future__ import annotations

import re as _re
import time

from app.core.logger import logger
from app.services.agents import redis_client

# Properties to remove before passing objects to LLM prompts.
# No embeddings, no internal metadata, no numeric graph scores.
_STRIP_PROPS = {
    "cohere_embedding", "source_hash",
    "pagerank_score", "betweenness_score", "wcc_component_id",
    "leiden_gamma", "modularity_contribution",
    "enrichment_status", "description_model", "embedding_model",
    "embedding_generated_at", "description_generated_at", "created_at", "updated_at",
    "ordinal_position", "null_frac", "n_distinct", "same_name_col_count",
    "encoded_pct", "size_mb", "type_confidence", "distkey_col", "diststyle",
    "sortkey_type", "sortkey1",
    "synonyms_text", "intent_tags_text", "top_values_text",
    "is_notnull", "is_nullable", "is_pii", "pii_type", "is_pk",
    "is_isolated", "is_subquery_anchor", "is_weakly_bridged",
    "ontology_class", "schema", "table_type_db", "version",
    "top_freq_values",
    "dominant_domain_confidence", "domain_distribution", "run_date",
    "anchor_ontology_classes", "intent_scores", "source_line", "time_windowed",
    "_join_critical",  # internal flag, not for LLM
}

# Domain keywords that trigger Path G (Domain vector search)
_DOMAIN_KEYWORDS = {
    "liquidity", "cash", "fx", "foreign exchange", "currency", "hedging",
    "debt", "borrowing", "loan", "credit", "interest", "rate", "investment",
    "payment", "transaction", "fraud", "sweep", "concentration",
    "banking", "treasury", "capital", "equity", "dividend",
}

_FOLLOWUP_SIGNALS = _re.compile(
    r"^("
    r"yes|yep|yup|yeah|sure|ok|okay|alright|absolutely|of course|definitely|"
    r"sounds good|great|perfect|proceed|continue|go ahead|do it|please|"
    r"show me|show that|more details?|more info|tell me more|elaborate|"
    r"break (it|that|this|them) down|drill (down|into)|"
    r"what about|and (also|then|what)|"
    r"(show|give|get|fetch|pull|display|list|what('s| is| are)) (me )?(the |those |that |it|them|more)"
    r")[\s.!?]*$",
    _re.IGNORECASE,
)
_SHORT_FOLLOWUP_WORD_LIMIT = 5


def is_followup_question(question: str, has_session_context: bool) -> bool:
    stripped = question.strip()
    if not stripped or len(stripped) <= 2:
        return True
    if _FOLLOWUP_SIGNALS.match(stripped):
        return True
    if has_session_context and len(stripped.split()) <= _SHORT_FOLLOWUP_WORD_LIMIT:
        return True
    return False


def reconstruct_question(question: str, session_summary: str, previous_follow_ups: list[str]) -> str:
    if previous_follow_ups:
        return previous_follow_ups[0]
    if session_summary and len(session_summary) > 20:
        return session_summary[-300:].strip()
    return question


def domain_keyword_detected(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _DOMAIN_KEYWORDS)


async def get_embedding(text: str) -> list[float]:
    normalized = text.strip().lower()
    cached = redis_client.get_embedding(normalized)
    if cached:
        logger.debug("cohere embed | cache_hit=True | ms=0")
        return cached
    t0 = time.monotonic()
    from app.services.embeddings import embed_question
    embedding = await embed_question(normalized)
    logger.debug("cohere embed | cache_hit=False | ms={:.0f}", (time.monotonic() - t0) * 1000)
    redis_client.set_embedding(normalized, embedding)
    return embedding


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in a single Cohere API call.

    Checks Redis cache per-text first. Only the cache-miss texts are sent to Cohere
    in one batched request. Results are stored back into Redis.
    Returns a list of embeddings in the same order as `texts`.
    Falls back to calling get_embedding() individually if batch call fails.
    """
    if not texts:
        return []

    normalized = [t.strip().lower() for t in texts]
    results: list[list[float] | None] = [None] * len(normalized)
    miss_indices: list[int] = []

    for i, key in enumerate(normalized):
        cached = redis_client.get_embedding(key)
        if cached:
            results[i] = cached
        else:
            miss_indices.append(i)

    if not miss_indices:
        logger.debug("cohere embed batch | all_cache_hits | count={}", len(texts))
        return results  # type: ignore[return-value]

    miss_texts = [normalized[i] for i in miss_indices]
    t0 = time.monotonic()
    try:
        from app.core.config import settings
        from app.services.embeddings import _get_async_client, _EMBED_URL, _COMMON_HEADERS
        client = await _get_async_client()
        resp = await client.post(
            _EMBED_URL,
            headers=_COMMON_HEADERS,
            json={
                "texts": [t[:2048] for t in miss_texts],
                "input_type": "search_query",
                "embedding_types": ["float"],
                "truncate": "END",
            },
        )
        resp.raise_for_status()
        batch_embeddings: list[list[float]] = resp.json()["embeddings"]["float"]
        logger.debug(
            "cohere embed batch | cache_misses={} | total={} | ms={:.0f}",
            len(miss_indices), len(texts), (time.monotonic() - t0) * 1000,
        )
        for idx, emb in zip(miss_indices, batch_embeddings):
            results[idx] = emb
            redis_client.set_embedding(normalized[idx], emb)
    except Exception as e:
        logger.warning("cohere embed batch failed, falling back to individual calls | error={}", e)
        for idx in miss_indices:
            results[idx] = await get_embedding(normalized[idx])

    return [r for r in results if r is not None]


def tokenize_with_bigrams(text: str) -> list[str]:
    words = _re.findall(r"\b\w+\b", text.lower())
    tokens = list(words)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        tokens.append(bigram)
        tokens.append(f"{words[i]}_{words[i+1]}")
    return tokens


def trim_objects(objects: list[dict], join_critical_cols: set[tuple] | None = None) -> list[dict]:
    """Strip internal Neo4j metadata from objects before passing to LLM prompts.

    Descriptions are kept in full — truncation was causing specialists to miss
    critical context (FK semantics, filter value meaning, grain information).
    Synonyms are kept in full for all columns.
    """
    trimmed = []
    for obj in objects:
        cleaned = {k: v for k, v in obj.items() if k not in _STRIP_PROPS}

        # List field limits — keep reasonable caps to avoid prompt bloat
        for list_field, limit in [
            ("sample_values", 5),
            ("value_vocabulary", 5),
            ("value_aliases", 5),
            ("variants", 5),
            ("natural_dimensions", 6),
            ("natural_measures", 6),
        ]:
            if list_field in cleaned and isinstance(cleaned[list_field], list):
                cleaned[list_field] = cleaned[list_field][:limit]

        trimmed.append(cleaned)
    return trimmed

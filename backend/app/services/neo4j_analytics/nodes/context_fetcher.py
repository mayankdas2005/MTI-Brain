"""Node 1a: context_fetcher — pure Neo4j retrieval, builds SemanticContext.

No LLM. Embeds the question via Cohere, searches Neo4j for templates/tables/columns,
fetches business terms and intents, applies community scoping, budget trims,
and injects short/long-term memory context.
"""

from __future__ import annotations

import hashlib
import json
import time

from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.memory import long_term, short_term
from app.services.neo4j_analytics.state import AnalyticsState

_STRIP_PROPS = {
    "cohere_embedding", "source_hash", "version", "pagerank_score", "betweenness_score",
    "wcc_component_id", "leiden_gamma", "modularity_contribution", "enrichment_status",
    "description_model", "ordinal_position", "null_frac", "n_distinct",
    "same_name_col_count", "encoded_pct", "size_mb", "type_confidence",
}


async def context_fetcher(state: AnalyticsState, config: dict) -> dict:
    logger.info("context_fetcher START | thread={} | question={}", state["thread_id"], state["question"][:80])

    try:
        embedding = await _get_embedding(state["question"])
        query_text = state["question"]

        templates = neo4j_client.search_query_templates(embedding)
        tables = neo4j_client.search_tables_fulltext(query_text)
        columns = neo4j_client.search_columns_fulltext(query_text)
        tokens = _tokenize_with_bigrams(query_text)
        business_terms = neo4j_client.lookup_business_terms(tokens)
        intents = neo4j_client.search_intents(embedding)

        tables = _apply_community_scoping(tables)
        templates = _trim_objects(templates)
        tables = _trim_objects(tables)
        columns = _trim_objects(columns)

        session_summary = short_term.get_session_summary(state["thread_id"])
        memory_context = await long_term.retrieve_user_memory(state["user_id"], state["question"])

        semantic_context = {
            "templates": templates,
            "tables": tables,
            "columns": columns,
            "business_terms": business_terms,
            "intents": intents,
            "session_summary": session_summary,
            "memory_context": memory_context,
        }

        logger.info(
            "context_fetcher DONE | thread={} | templates={} | tables={} | cols={}",
            state["thread_id"], len(templates), len(tables), len(columns),
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

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


def tokenize_with_bigrams(text: str) -> list[str]:
    words = _re.findall(r"\b\w+\b", text.lower())
    tokens = list(words)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        tokens.append(bigram)
        tokens.append(f"{words[i]}_{words[i+1]}")
    return tokens


def trim_objects(objects: list[dict], join_critical_cols: set[tuple] | None = None) -> list[dict]:
    """Trim objects for LLM display.

    Description truncation varies by column type:
    - Join-critical columns: full description (not truncated) + synonyms shown
    - Measurable columns: 100 chars
    - Others: 60 chars
    """
    trimmed = []
    for obj in objects:
        cleaned = {k: v for k, v in obj.items() if k not in _STRIP_PROPS}

        # Description truncation — per column priority
        if "description" in cleaned and isinstance(cleaned["description"], str):
            fqn = cleaned.get("table_fqn", "")
            name = cleaned.get("name", "")
            key = (fqn, name)
            if join_critical_cols and key in join_critical_cols:
                pass  # keep full description for join-critical columns
            elif cleaned.get("is_measurable"):
                cleaned["description"] = cleaned["description"][:100]
            else:
                cleaned["description"] = cleaned["description"][:60]

        # List field limits
        for list_field, limit in [
            ("sample_values", 5),
            ("value_vocabulary", 5),  # trimmed for LLM display; _column_lookup has full data
            ("value_aliases", 5),
            ("variants", 5),
            ("natural_dimensions", 6),
            ("natural_measures", 6),
        ]:
            if list_field in cleaned and isinstance(cleaned[list_field], list):
                cleaned[list_field] = cleaned[list_field][:limit]

        # Synonyms: show for join-critical (up to 3) and measurable (up to 2)
        if "synonyms" in cleaned and isinstance(cleaned["synonyms"], list):
            fqn = cleaned.get("table_fqn", "")
            name = cleaned.get("name", "")
            key = (fqn, name)
            if join_critical_cols and key in join_critical_cols:
                cleaned["synonyms"] = cleaned["synonyms"][:3]
            elif cleaned.get("is_measurable"):
                cleaned["synonyms"] = cleaned["synonyms"][:2]
            else:
                cleaned.pop("synonyms", None)  # don't show synonyms for non-critical columns

        trimmed.append(cleaned)
    return trimmed

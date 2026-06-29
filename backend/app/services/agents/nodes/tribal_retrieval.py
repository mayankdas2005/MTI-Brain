"""Node: tribal_retrieval — multi-term hybrid pgvector + FTS retrieval from tribal knowledge store.

When deep_analysis=True:
  - Reads search_terms, search_variants, and entity_tokens from state (set by intake_classifier).
  - Embeds [question, *search_terms] concurrently via Cohere.
  - For each query string runs BOTH a vector search and a websearch_to_tsquery FTS search.
  - Merges all ranked lists via multi-source Reciprocal Rank Fusion → top 12 docs.

Fallback: if the pgvector table is empty (ingestion script not yet run), falls back
to the original Neo4j keyword search so the pipeline degrades gracefully.

Results stored in state["tribal_facts"] and injected into the synthesis prompt.
Non-fatal: any error returns empty list and pipeline continues normally.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig
from sqlalchemy import text

from app.core.logger import logger
from app.db.session import async_read_session_factory
from app.services.agents.state import AnalyticsState
from app.services.embeddings import embed_question

_TRIBAL_LABELS = ["Policy", "Limit", "Decision", "Commitment", "Watchlist"]

_SKIP_WORDS = frozenset({
    "what", "show", "give", "list", "find", "that", "this", "with",
    "from", "have", "does", "when", "where", "which", "about",
})

_CYPHER = """
MATCH (n)
WHERE any(lbl IN labels(n) WHERE lbl IN $labels)
  AND (
    toLower(coalesce(n.name, ''))  CONTAINS $kw1 OR
    toLower(coalesce(n.label, '')) CONTAINS $kw1 OR
    toLower(coalesce(n.name, ''))  CONTAINS $kw2 OR
    toLower(coalesce(n.label, '')) CONTAINS $kw2
  )
  AND (n.status IS NULL OR toLower(n.status) = 'active')
RETURN labels(n)[0]                           AS type,
       coalesce(n.name, n.label, '')          AS label,
       coalesce(n.value, n.limit_value, '')   AS value,
       coalesce(n.status, '')                 AS status,
       coalesce(n.effective_from, '')         AS effectiveFrom,
       coalesce(n.effective_to, '')           AS effectiveTo
LIMIT 20
"""

_COUNT_SQL = text("SELECT COUNT(*) FROM mti_brain_tribal_knowledge")

_VECTOR_SQL = text("""
    SELECT source_file, file_name, folder, content,
           1 - (embedding <=> CAST(:embedding AS vector)) AS score
    FROM mti_brain_tribal_knowledge
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:embedding AS vector)
    LIMIT 10
""")

_FTS_SQL = text("""
    SELECT source_file, file_name, folder, content,
           ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) AS score
    FROM mti_brain_tribal_knowledge
    WHERE search_vector @@ websearch_to_tsquery('english', :query)
    ORDER BY score DESC
    LIMIT 10
""")


def _rrf_merge_multi(
    ranked_lists: list[list[dict]],
    k: int = 60,
    top_n: int = 12,
) -> list[dict]:
    """Multi-source Reciprocal Rank Fusion over N ranked lists keyed by source_file.

    Each list contributes 1/(k + rank + 1) to the shared score dict.
    A document found by multiple passes (e.g. full-question vector + focused-term FTS)
    accumulates score from each, naturally surfacing the most relevant docs.
    """
    scores: dict[str, float] = {}
    by_key: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, row in enumerate(ranked):
            key = row["source_file"]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_key.setdefault(key, row)
    ranked_keys = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result = []
    for key, score in ranked_keys[:top_n]:
        row = dict(by_key[key])
        row["_rrf_score"] = score
        result.append(row)
    return result


async def _hybrid_retrieval(question: str, search_terms: list[str]) -> list[dict] | None:
    """Run multi-term hybrid vector+FTS search.

    Embeds [question, *search_terms] concurrently, runs vector and FTS per query string,
    then merges all ranked lists via multi-source RRF.
    Returns None if the table is empty (not seeded).
    """
    query_strings = [question] + [t for t in search_terms if t and t != question]

    embeddings = await asyncio.gather(*[embed_question(q) for q in query_strings])

    async with async_read_session_factory() as db:
        count = (await db.execute(_COUNT_SQL)).scalar() or 0
        if count == 0:
            return None

        ranked_lists: list[list[dict]] = []
        for q_str, embedding in zip(query_strings, embeddings):
            if embedding is not None:
                vec_result = await db.execute(_VECTOR_SQL, {"embedding": str(embedding)})
                vec_rows = [dict(r._mapping) for r in vec_result]
                if vec_rows:
                    ranked_lists.append(vec_rows)

            try:
                fts_result = await db.execute(_FTS_SQL, {"query": q_str})
                fts_rows = [dict(r._mapping) for r in fts_result]
                if fts_rows:
                    ranked_lists.append(fts_rows)
            except Exception:
                pass

    if not ranked_lists:
        return []

    merged = _rrf_merge_multi(ranked_lists)
    max_score = merged[0]["_rrf_score"] if merged else 1.0
    return [
        {
            "type": "TribalKnowledge",
            "label": row["file_name"],
            "value": row["content"][:4000],
            "status": "active",
            "score": round(row["_rrf_score"] / max_score, 3),
        }
        for row in merged
    ]


def _extract_keywords(question: str) -> tuple[str, str]:
    words = [
        w for w in question.lower().split()
        if len(w) > 4 and w not in _SKIP_WORDS
    ]
    kw1 = words[0] if words else "limit"
    kw2 = words[1] if len(words) > 1 else "policy"
    return kw1, kw2


def _run_cypher(kw1: str, kw2: str) -> list[dict]:
    from app.services.agents import neo4j_client
    rows = neo4j_client._neo4j_run(
        _CYPHER,
        {"labels": _TRIBAL_LABELS, "kw1": kw1, "kw2": kw2},
    )
    return [
        {
            "type": row["type"],
            "label": row["label"],
            "value": row["value"],
            "status": row["status"],
            "effectiveFrom": row["effectiveFrom"],
            "effectiveTo": row["effectiveTo"],
        }
        for row in rows
    ]


async def tribal_retrieval(state: AnalyticsState, config: RunnableConfig) -> dict:
    if not state.get("deep_analysis"):
        return {"tribal_facts": []}

    question = state.get("question", "")
    search_terms = list(state.get("search_terms") or [])
    search_variants = list(state.get("search_variants") or [])
    entity_tokens = list(state.get("entity_tokens") or [])

    # If intake_classifier didn't populate search_terms, fall back to search_variants / entity_tokens
    if not search_terms:
        search_terms = (search_variants + entity_tokens)[:3]

    num_terms = len(search_terms)

    # Primary: multi-term hybrid search (vector + FTS via multi-source RRF)
    try:
        facts = await _hybrid_retrieval(question, search_terms)
        if facts is not None:
            logger.info(
                "tribal_retrieval | hybrid | thread={} | pgvector_terms={} | found={}",
                state["thread_id"], 1 + num_terms, len(facts),
            )
            return {"tribal_facts": facts}
        logger.info(
            "tribal_retrieval | pgvector table empty, falling back to neo4j | thread={}",
            state["thread_id"],
        )
    except Exception as e:
        logger.warning(
            "tribal_retrieval hybrid failed (non-fatal) | thread={} | error={}",
            state["thread_id"], e,
        )

    # Fallback: Neo4j keyword search (only when pgvector table is empty)
    kw1, kw2 = _extract_keywords(question)
    try:
        facts = await asyncio.to_thread(_run_cypher, kw1, kw2)
        logger.info(
            "tribal_retrieval | neo4j fallback | thread={} | kw1={} | kw2={} | found={}",
            state["thread_id"], kw1, kw2, len(facts),
        )
    except Exception as e:
        logger.warning(
            "tribal_retrieval neo4j fallback failed (non-fatal) | thread={} | error={}",
            state["thread_id"], e,
        )
        facts = []

    return {"tribal_facts": facts}

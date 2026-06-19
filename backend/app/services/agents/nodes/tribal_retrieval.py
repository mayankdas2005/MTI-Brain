"""Node: tribal_retrieval — hybrid pgvector + FTS retrieval from tribal knowledge store.

Primary path (when deep_analysis=True): embeds the question via Cohere and runs a
hybrid search against mti_brain_tribal_knowledge — vector cosine similarity (top 5)
merged with PostgreSQL full-text search (top 5) via Reciprocal Rank Fusion.

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
    LIMIT 5
""")

_FTS_SQL = text("""
    SELECT source_file, file_name, folder, content,
           ts_rank(search_vector, plainto_tsquery('english', :query)) AS score
    FROM mti_brain_tribal_knowledge
    WHERE search_vector @@ plainto_tsquery('english', :query)
    ORDER BY score DESC
    LIMIT 5
""")


def _rrf_merge(
    vector_rows: list[dict],
    fts_rows: list[dict],
    k: int = 60,
    top_n: int = 8,
) -> list[dict]:
    """Reciprocal Rank Fusion over two ranked lists keyed by source_file."""
    scores: dict[str, float] = {}
    for rank, row in enumerate(vector_rows):
        key = row["source_file"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    for rank, row in enumerate(fts_rows):
        key = row["source_file"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    by_key: dict[str, dict] = {}
    for row in (*vector_rows, *fts_rows):
        by_key.setdefault(row["source_file"], row)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [by_key[k] for k, _ in ranked[:top_n]]


async def _pgvector_retrieval(question: str) -> list[dict] | None:
    """Run hybrid vector+FTS search. Returns None if the table is empty (not seeded)."""
    embedding = await embed_question(question)
    if embedding is None:
        return []

    embedding_str = str(embedding)

    async with async_read_session_factory() as db:
        count = (await db.execute(_COUNT_SQL)).scalar() or 0
        if count == 0:
            return None

        vec_result = await db.execute(_VECTOR_SQL, {"embedding": embedding_str})
        vec_rows = [dict(r._mapping) for r in vec_result]

        fts_result = await db.execute(_FTS_SQL, {"query": question})
        fts_rows = [dict(r._mapping) for r in fts_result]

    merged = _rrf_merge(vec_rows, fts_rows)
    return [
        {
            "type": "TribalKnowledge",
            "label": row["file_name"],
            "value": row["content"][:2000],
            "status": "active",
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

    # Primary: pgvector hybrid search (vector + FTS via RRF)
    try:
        facts = await _pgvector_retrieval(question)
        if facts is not None:
            logger.info(
                "tribal_retrieval | pgvector | thread={} | found={}",
                state["thread_id"], len(facts),
            )
            return {"tribal_facts": facts}
        logger.info(
            "tribal_retrieval | pgvector table empty, falling back to neo4j | thread={}",
            state["thread_id"],
        )
    except Exception as e:
        logger.warning(
            "tribal_retrieval pgvector failed (non-fatal) | thread={} | error={}",
            state["thread_id"], e,
        )

    # Fallback: Neo4j keyword search
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

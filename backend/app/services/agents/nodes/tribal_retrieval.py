"""Node: tribal_retrieval — fetch policy/limit/decision facts from Neo4j tribal graph.

Runs only when deep_analysis is True. Queries Neo4j for nodes labeled Policy,
Limit, Decision, Commitment, or Watchlist matching keywords from the question.
Results stored in state["tribal_facts"] and injected into the synthesis prompt.
Non-fatal: any Neo4j error returns empty list and pipeline continues normally.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.state import AnalyticsState

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
    kw1, kw2 = _extract_keywords(question)

    try:
        facts = await asyncio.to_thread(_run_cypher, kw1, kw2)
        logger.info(
            "tribal_retrieval | thread={} | kw1={} | kw2={} | found={}",
            state["thread_id"], kw1, kw2, len(facts),
        )
    except Exception as e:
        logger.warning(
            "tribal_retrieval failed (non-fatal) | thread={} | error={}",
            state["thread_id"], e,
        )
        facts = []

    return {"tribal_facts": facts}

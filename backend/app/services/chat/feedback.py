"""Feedback service — persistence and retrieval for user thumbs-up/down."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

from app.core.logger import logger
from app.models.conversation import MTIBrainFeedback
from app.services.embeddings import embed_question
from sqlalchemy import ARRAY, Text, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

_FEEDBACK_DECAY_HALF_LIFE_DAYS = 45   # feedback weight halves every 45 days
_FEEDBACK_DROP_WEIGHT = 0.05          # drop items below this weight (~180 days)
_FEEDBACK_OLD_DAYS = 30               # annotate items older than this

_RRF_K = 60


# ── Intent text helpers ───────────────────────────────────────────────────────

def _build_intent_text(raw_fingerprint_json: str | None) -> str | None:
    """Build a FTS-searchable string from a stored intent_fingerprint JSON blob."""
    if not raw_fingerprint_json:
        return None
    try:
        fp = json.loads(raw_fingerprint_json)
    except Exception:
        return None
    parts = [
        " ".join(fp.get("anchor_tables") or []),
        " ".join(fp.get("measures") or []),
        " ".join(fp.get("filters") or []),
        " ".join(fp.get("dimensions") or []),
        fp.get("time_period") or "",
    ]
    return " | ".join(p for p in parts if p) or None


# ── Save ──────────────────────────────────────────────────────────────────────

async def save_feedback(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    thread_id: uuid.UUID,
    liked: bool,
    comment: str | None = None,
    feedback_type: str = "general",
    intent_text: str | None = None,
) -> tuple[MTIBrainFeedback, str | None, str | None, dict | None]:
    """Save feedback and embed the question for future similarity search.

    Returns ``(feedback, langfuse_trace_id, pattern_id, neo4j_context)``
    neo4j_context actions:
      'dislike'              → write AntiPattern (sql/general feedback_type only)
      'like_without_pattern' → retroactively write QueryPattern
      None                   → no Neo4j write needed
    """
    result = await db.execute(
        text(
            "SELECT id, "
            "       metadata->>'langfuse_trace_id' AS langfuse_trace_id, "
            "       metadata->>'pattern_id' AS pattern_id, "
            "       metadata->>'sql' AS sql, "
            "       metadata->>'tables_used' AS tables_used, "
            "       metadata->>'intent' AS intent, "
            "       metadata->>'complexity' AS complexity, "
            "       metadata->'confidence'->>'score' AS confidence_score "
            "FROM mti_brain_message "
            "WHERE conversation_id = :cid AND role = 'assistant' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    row = result.one_or_none()
    message_id = row.id if row else None
    langfuse_trace_id: str | None = row.langfuse_trace_id if row else None
    pattern_id: str | None = row.pattern_id if row else None

    _sql = (row.sql or "") if row else ""
    _tables = (row.tables_used or "") if row else ""
    _intent = (row.intent or "") if row else ""
    _complexity = (row.complexity or "") if row else ""
    _conf_score = int(row.confidence_score) if (row and row.confidence_score) else 0
    _tables_list: list[str] = [t.strip() for t in _tables.split(",") if t.strip()] if _tables else []

    question_result = await db.execute(
        text(
            "SELECT content FROM mti_brain_message "
            "WHERE conversation_id = :cid AND role = 'user' LIMIT 1"
        ),
        {"cid": str(conversation_id)},
    )
    question_text = question_result.scalar_one_or_none() or ""
    embedding = await embed_question(question_text) if question_text else None

    existing: MTIBrainFeedback | None = None
    if message_id:
        existing = (await db.execute(
            select(MTIBrainFeedback).where(MTIBrainFeedback.message_id == message_id)
        )).scalar_one_or_none()

    _ftype = feedback_type or "general"

    if existing:
        existing.liked = liked
        existing.comment = comment
        existing.question_text = question_text or None
        existing.feedback_type = _ftype
        if intent_text is not None:
            existing.intent_text = intent_text
        if embedding is not None:
            existing.embedding = embedding
        if _tables_list:
            existing.tables_used = _tables_list
        feedback = existing
        logger.info(
            "Feedback updated: conversation={}, liked={}, type={}", conversation_id, liked, _ftype
        )
    else:
        feedback = MTIBrainFeedback(
            message_id=message_id,
            thread_id=thread_id,
            liked=liked,
            comment=comment,
            question_text=question_text or None,
            intent_text=intent_text,
            feedback_type=_ftype,
            embedding=embedding,
            tables_used=_tables_list or None,
        )
        db.add(feedback)
        logger.info(
            "Feedback saved: conversation={}, liked={}, type={}, has_embedding={}",
            conversation_id, liked, _ftype, embedding is not None,
        )

    await db.flush()

    # Only sql/general dislikes write to Neo4j — answer/chart dislikes are presentation-only
    _is_sql_signal = _ftype in ("sql", "general")

    neo4j_context: dict | None = None
    if not liked and _is_sql_signal:
        neo4j_context = {
            "action": "dislike",
            "question": question_text,
            "sql": _sql,
            "tables_used": _tables,
            "intent": _intent,
            "complexity": _complexity,
            "embedding": embedding,
            "comment": comment,
            "feedback_type": _ftype,
        }
    elif liked and not pattern_id:
        neo4j_context = {
            "action": "like_without_pattern",
            "question": question_text,
            "sql": _sql,
            "tables_used": _tables,
            "intent": _intent,
            "complexity": _complexity,
            "confidence_score": _conf_score,
            "embedding": embedding,
            "feedback_type": _ftype,
        }

    return feedback, langfuse_trace_id, pattern_id, neo4j_context


# ── Thread feedback (all same-thread feedback applies to all subsequent queries) ──

_FIND_THREAD_FEEDBACK_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text,
        f.feedback_type
    FROM mti_brain_feedback f
    WHERE f.thread_id = :thread_id
    ORDER BY f.created_at DESC
    LIMIT :limit
""")


async def find_thread_feedback(
    db: AsyncSession,
    thread_id: uuid.UUID,
    limit: int = 5,
) -> list[dict]:
    """Get all feedback from the current thread."""
    result = await db.execute(
        _FIND_THREAD_FEEDBACK_SQL,
        {"thread_id": str(thread_id), "limit": limit},
    )
    return [
        {
            "id": str(row.id),
            "liked": row.liked,
            "comment": row.comment,
            "thread_id": str(row.thread_id),
            "created_at": row.created_at,
            "question_text": (row.question_text or "")[:200],
            "feedback_type": row.feedback_type or "general",
            "source": "thread",
        }
        for row in result.fetchall()
    ]


# ── Cross-thread hybrid search ────────────────────────────────────────────────

_FIND_SIMILAR_VECTOR_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text,
        f.feedback_type,
        1 - (f.embedding <=> CAST(:embedding AS vector)) AS score
    FROM mti_brain_feedback f
    WHERE f.embedding IS NOT NULL
      AND f.thread_id != :current_thread_id
      AND 1 - (f.embedding <=> CAST(:embedding AS vector)) >= :min_similarity
    ORDER BY score DESC
    LIMIT :limit
""")

_FIND_SIMILAR_FTS_SQL = text("""
    SELECT
        f.id,
        f.liked,
        f.comment,
        f.thread_id,
        f.created_at,
        f.question_text,
        f.feedback_type,
        ts_rank_cd(f.search_vector, websearch_to_tsquery('english', :query)) AS score
    FROM mti_brain_feedback f
    WHERE f.search_vector IS NOT NULL
      AND f.thread_id != :current_thread_id
      AND f.search_vector @@ websearch_to_tsquery('english', :query)
    ORDER BY score DESC
    LIMIT :limit
""")


def _rrf_merge(
    ranked_lists: list[list[dict]],
    k: int = _RRF_K,
    top_n: int = 5,
) -> list[dict]:
    scores: dict[str, float] = {}
    by_key: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, row in enumerate(ranked):
            key = row["id"]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_key.setdefault(key, row)
    ranked_keys = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result = []
    for key, score in ranked_keys[:top_n]:
        row = dict(by_key[key])
        row["_rrf_score"] = score
        result.append(row)
    return result


_FIND_SIMILAR_TABLES_SQL = text("""
    SELECT id, liked, comment, thread_id, created_at, question_text, feedback_type, score
    FROM (
        SELECT
            f.id, f.liked, f.comment, f.thread_id, f.created_at,
            f.question_text, f.feedback_type,
            (
                SELECT COUNT(*)::float
                FROM unnest(f.tables_used) t
                WHERE t = ANY(:anchor_tables)
            ) / GREATEST(cardinality(f.tables_used), 1) AS score
        FROM mti_brain_feedback f
        WHERE f.tables_used IS NOT NULL
          AND cardinality(f.tables_used) > 0
          AND f.tables_used && :anchor_tables
          AND f.thread_id != :current_thread_id
    ) sub
    WHERE score >= 0.5
    ORDER BY score DESC
    LIMIT :limit
""").bindparams(bindparam("anchor_tables", type_=ARRAY(Text)))


async def find_feedback_by_tables(
    db: AsyncSession,
    anchor_tables: list[str],
    current_thread_id: uuid.UUID,
    limit: int = 5,
) -> list[dict]:
    """Find cross-thread feedback whose stored tables_used overlaps >= 50% with anchor_tables.

    Path B of hybrid feedback retrieval — complements the early vector+FTS pass in
    lt_memory_retriever.  Called late (from sql_generator) once anchor_tables_resolved
    is populated.  anchor_tables is passed as a Python list; bindparam(ARRAY(Text))
    tells asyncpg the correct wire type.
    """
    if not anchor_tables:
        return []
    try:
        result = await db.execute(
            _FIND_SIMILAR_TABLES_SQL,
            {
                "anchor_tables": list(anchor_tables),
                "current_thread_id": str(current_thread_id),
                "limit": limit,
            },
        )
        return [
            {
                "id": str(r.id),
                "liked": r.liked,
                "comment": r.comment,
                "thread_id": str(r.thread_id),
                "created_at": r.created_at,
                "question_text": (r.question_text or "")[:200],
                "feedback_type": r.feedback_type or "general",
                "similarity": round(float(r.score), 3),
                "source": "similar_tables",
            }
            for r in result.fetchall()
        ]
    except Exception as exc:
        logger.warning(
            "find_feedback_by_tables | failed | err={}: {}", type(exc).__name__, exc
        )
        return []


async def find_similar_feedback(
    db: AsyncSession,
    question: str,
    current_thread_id: uuid.UUID,
    limit: int = 5,
    min_similarity: float = 0.60,
) -> list[dict]:
    """Find feedback from OTHER threads matching the current question (hybrid: vector + FTS + RRF)."""
    ranked_lists: list[list[dict]] = []

    embedding = await embed_question(question)
    if embedding is not None:
        try:
            vec_result = await db.execute(
                _FIND_SIMILAR_VECTOR_SQL,
                {
                    "embedding": str(embedding),
                    "current_thread_id": str(current_thread_id),
                    "limit": limit * 2,
                    "min_similarity": min_similarity,
                },
            )
            vec_rows = [
                {
                    "id": str(r.id),
                    "liked": r.liked,
                    "comment": r.comment,
                    "thread_id": str(r.thread_id),
                    "created_at": r.created_at,
                    "question_text": (r.question_text or "")[:200],
                    "feedback_type": r.feedback_type or "general",
                    "similarity": round(float(r.score), 3),
                }
                for r in vec_result.fetchall()
            ]
            if vec_rows:
                ranked_lists.append(vec_rows)
        except Exception as exc:
            logger.warning("find_similar_feedback | vector_leg_failed | err={}: {}", type(exc).__name__, exc)

    try:
        fts_result = await db.execute(
            _FIND_SIMILAR_FTS_SQL,
            {
                "query": question,
                "current_thread_id": str(current_thread_id),
                "limit": limit * 2,
            },
        )
        fts_rows = [
            {
                "id": str(r.id),
                "liked": r.liked,
                "comment": r.comment,
                "thread_id": str(r.thread_id),
                "created_at": r.created_at,
                "question_text": (r.question_text or "")[:200],
                "feedback_type": r.feedback_type or "general",
                "similarity": None,
            }
            for r in fts_result.fetchall()
        ]
        if fts_rows:
            ranked_lists.append(fts_rows)
    except Exception as exc:
        logger.warning("find_similar_feedback | fts_leg_failed | err={}: {}", type(exc).__name__, exc)

    if not ranked_lists:
        return []

    merged = _rrf_merge(ranked_lists, top_n=limit)
    return [{**row, "source": "similar"} for row in merged]


# ── Context building ──────────────────────────────────────────────────────────

def _days_old(created_at: datetime | None) -> int:
    if created_at is None:
        return 0
    now = datetime.now(timezone.utc)
    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    return max(0, (now - ts).days)


def _decay_weight(created_at: datetime | None) -> float:
    age = _days_old(created_at)
    return math.exp(-0.693 * age / _FEEDBACK_DECAY_HALF_LIFE_DAYS)


def _resolve_contradictions(feedback_items: list[dict]) -> list[dict]:
    """Keep only the most recent item when contradictory same-topic feedback exists."""
    def _topic_key(fb: dict) -> str:
        words = (fb.get("comment") or "").lower().split()
        significant = [w for w in words if len(w) > 3]
        return " ".join(significant[:6])

    by_topic: dict[str, dict] = {}
    for fb in feedback_items:
        if not fb.get("comment"):
            continue
        key = _topic_key(fb)
        if not key:
            continue
        existing = by_topic.get(key)
        if existing is None:
            by_topic[key] = fb
        else:
            ts_existing = existing.get("created_at")
            ts_new = fb.get("created_at")
            if ts_new and ts_existing and ts_new > ts_existing:
                if existing["liked"] != fb["liked"]:
                    logger.debug("feedback | contradiction_resolved | topic={}", key)
                by_topic[key] = fb

    no_comment = [fb for fb in feedback_items if not fb.get("comment")]
    return list(by_topic.values()) + no_comment


def build_feedback_context(
    thread_feedback: list[dict],
    similar_feedback: list[dict],
) -> list[dict]:
    """Merge thread + cross-thread feedback, dedup, apply decay threshold.

    Returns list[dict] — callers use build_feedback_context_for_node to get
    a node-type-filtered string for injection into an LLM prompt.
    """
    seen_ids = {f["id"] for f in thread_feedback}
    similar_deduped = [f for f in similar_feedback if f["id"] not in seen_ids]
    all_feedback = thread_feedback + similar_deduped
    return [f for f in all_feedback if _decay_weight(f.get("created_at")) >= _FEEDBACK_DROP_WEIGHT]


def build_feedback_context_for_node(all_feedback: list[dict], node_type: str) -> str:
    """Build a type-filtered LLM-injectable feedback string for a specific node.

    node_type routing:
      'sql'     → feedback_type IN ('sql', 'general')
                  → anchor_resolver, filter/measure/dimension specialists, directive_writer, sql_generator
      'answer'  → feedback_type IN ('answer', 'general')  → synthesis
      'chart'   → feedback_type IN ('chart', 'general')   → chart_agent
      'general' → feedback_type == 'general' only          → general_chat
    """
    _allowed: dict[str, set[str]] = {
        "sql":     {"sql", "general"},
        "answer":  {"answer", "general"},
        "chart":   {"chart", "general"},
        "general": {"general"},
    }
    allowed = _allowed.get(node_type, {"general"})
    filtered = [f for f in all_feedback if (f.get("feedback_type") or "general") in allowed]
    return _build_context_string(filtered)


def _build_context_string(feedback_items: list[dict]) -> str:
    """Build the final prompt-injectable string from a pre-filtered list.

    Items with ``_distilled=True`` are rendered as a DISTILLED PREFERENCE PROFILE
    preamble; remaining items are rendered as the standard feedback block.
    """
    if not feedback_items:
        return ""

    distilled = [f for f in feedback_items if f.get("_distilled")]
    regular   = [f for f in feedback_items if not f.get("_distilled")]

    output_parts: list[str] = []

    if distilled:
        profile = "\n".join(item["comment"] for item in distilled if item.get("comment"))
        if profile:
            output_parts.append(
                "DISTILLED USER PREFERENCE PROFILE (apply every point to your response):\n"
                + profile
            )

    if regular:
        weighted = sorted(
            regular,
            key=lambda f: (_decay_weight(f.get("created_at")), 1 if f.get("comment") else 0),
            reverse=True,
        )
        weighted = _resolve_contradictions(weighted)
        injectable = [f for f in weighted if f.get("comment")]

        if injectable:
            dislikes = [f for f in injectable if not f["liked"]]
            likes    = [f for f in injectable if f["liked"]]

            if dislikes or likes:
                def _label(fb: dict) -> str:
                    age      = _days_old(fb.get("created_at"))
                    source   = "this thread" if fb.get("source") == "thread" else "similar question"
                    age_note = f" (~{age}d ago)" if age > _FEEDBACK_OLD_DAYS else ""
                    return f"    - [{source}]{age_note} {fb['comment']}"

                lines: list[str] = ["USER FEEDBACK (apply to your response):"]
                if dislikes:
                    lines.append("  <hard_constraints>")
                    lines.append("  NEVER DO (users explicitly rejected this):")
                    for fb in dislikes[:5]:
                        lines.append(_label(fb))
                    lines.append("  </hard_constraints>")
                if likes:
                    lines.append("  <preferences>")
                    lines.append("  PREFERRED PATTERNS (users liked this approach):")
                    for fb in likes[:3]:
                        lines.append(_label(fb))
                    lines.append("  </preferences>")
                output_parts.append("\n".join(lines))

    return "\n\n".join(output_parts)

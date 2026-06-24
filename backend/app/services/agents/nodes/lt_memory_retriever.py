"""Node: lt_memory_retriever — fetch long-term memory and user feedback.

Runs as the FIRST node (before intake_classifier) so every downstream node —
regardless of question type, is_retry, or is_refinement — has feedback and
memory available.

Covers:
  • Long-term memory  : LangGraph PostgresStore — semantically similar past
                        interactions for this user (up to 3)
  • Thread feedback   : Rated responses (liked/disliked + comment) from THIS thread
  • Cross-thread      : Similar questions from OTHER threads that had ratings

NOT covered here:
  • Short-term session context — managed by LangGraph checkpointers automatically;
    context_fetcher reads session_summary via short_term.get_session_summary()
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.state import AnalyticsState


async def _mark_feedback_triggered(feedback_ids: list[str]) -> None:
    """Background: increment trigger_count + set last_triggered_at on retrieved feedback rows."""
    if not feedback_ids:
        return
    try:
        from app.db import async_session_factory
        from sqlalchemy import text
        async with async_session_factory() as db:
            placeholders = ", ".join(f":id{i}" for i in range(len(feedback_ids)))
            params = {f"id{i}": fid for i, fid in enumerate(feedback_ids)}
            await db.execute(
                text(
                    "UPDATE mti_brain_feedback "
                    "SET trigger_count = trigger_count + 1, "
                    "    last_triggered_at = now() "
                    f"WHERE id::text IN ({placeholders})"
                ),
                params,
            )
            await db.commit()
    except Exception as exc:
        logger.warning("lt_memory_retriever | mark_triggered_failed | err={}: {}", type(exc).__name__, exc)


async def lt_memory_retriever(state: AnalyticsState, config: RunnableConfig) -> dict:
    user_id               = state.get("user_id") or ""
    thread_id             = state.get("thread_id") or ""
    question              = state.get("question") or ""
    distilled_preferences = state.get("distilled_preferences") or ""

    async def _lt_memory() -> str:
        # LT memory disabled — PostgresStore not stable; re-enable when store is confirmed healthy
        return ""

    async def _thread_fb() -> list:
        try:
            from app.db import async_session_factory
            from app.services.chat.feedback import find_thread_feedback
            async with async_session_factory() as db:
                rows = await asyncio.wait_for(
                    find_thread_feedback(db, thread_id, limit=5), timeout=15.0
                )
                return [{**r, "source": "thread"} for r in (rows or [])]
        except Exception as exc:
            logger.warning("lt_memory_retriever | thread_fb_failed | thread={} | err={}: {}", thread_id, type(exc).__name__, exc)
            return []

    async def _similar_fb() -> list:
        try:
            from app.db import async_session_factory
            from app.services.chat.feedback import find_similar_feedback
            async with async_session_factory() as db:
                rows = await asyncio.wait_for(
                    find_similar_feedback(db, question, current_thread_id=thread_id, limit=5), timeout=15.0
                )
                return [{**r, "source": "similar"} for r in (rows or [])]
        except Exception as exc:
            logger.warning(
                "lt_memory_retriever | similar_fb_failed | thread={} | err={}: {}",
                thread_id, type(exc).__name__, exc,
            )
            return []

    lt_mem, thread_feedback, similar_feedback = await asyncio.gather(
        _lt_memory(), _thread_fb(), _similar_fb()
    )

    # Fire background trigger count update for all retrieved feedback rows
    all_retrieved_ids = [
        f["id"] for f in (thread_feedback or []) + (similar_feedback or [])
        if f.get("id")
    ]
    if all_retrieved_ids:
        asyncio.create_task(_mark_feedback_triggered(all_retrieved_ids))

    from app.services.chat.feedback import build_feedback_context

    # Distilled preferences branch: if a fresh distilled profile is present in state
    # (freshness is validated in chat.py before pipeline init), skip raw feedback
    # injection — nodes will use distilled_preferences from state instead.
    if distilled_preferences:
        feedback_context: list[dict] = []
        _distilled_active = True
    else:
        feedback_context = build_feedback_context(thread_feedback or [], similar_feedback or [])
        _distilled_active = False

    # Count LT memory items (each line is one recalled interaction)
    lt_mem_count = len([l for l in (lt_mem or "").split("\n") if l.strip()]) if lt_mem else 0

    thread_fb_count  = len(thread_feedback or [])
    similar_fb_count = len(similar_feedback or [])
    thread_liked     = sum(1 for f in (thread_feedback or []) if f.get("liked"))
    thread_disliked  = thread_fb_count - thread_liked

    # Structured summary for the about panel
    all_feedback = (thread_feedback or []) + (similar_feedback or [])
    _distilled_rules: list[str] = []
    if _distilled_active and distilled_preferences:
        _distilled_rules = [
            l.strip().lstrip("-•* \t").strip()
            for l in distilled_preferences.strip().splitlines()
            if l.strip()
        ]
    preference_summary = {
        "long_term_memory_applied": bool(lt_mem),
        "long_term_memory_count":   lt_mem_count,
        "thread_feedback_count":    thread_fb_count,
        "similar_feedback_count":   similar_fb_count,
        "feedback_applied":         bool(feedback_context) or _distilled_active,
        "distilled_active":         _distilled_active,
        "distilled_rules":          _distilled_rules,
        "feedback_items": [
            {
                "liked":            f["liked"],
                "comment":          f.get("comment") or None,
                "source":           f.get("source", "thread"),
                "question_preview": (f.get("question_text") or "")[:80].strip(),
                "similarity":       round(float(f["similarity"]), 2) if f.get("similarity") else None,
            }
            for f in all_feedback[:5]
            if f.get("liked") is not None
        ],
    }

    # ── Natural language label for the UI pipeline step ───────────────────────
    _lines = []

    if _distilled_active:
        if _distilled_rules:
            _lines.append("- **Behavioural rules in effect:**")
            for _rule in _distilled_rules[:8]:
                _lines.append(f"  - {_rule}")
        else:
            _lines.append("- **Distilled profile active**")

    if thread_fb_count > 0:
        _liked_str   = f"{thread_liked} liked" if thread_liked > 0 else ""
        _dislike_str = f"{thread_disliked} disliked" if thread_disliked > 0 else ""
        _detail      = " · ".join(filter(None, [_liked_str, _dislike_str]))
        _lines.append(
            f"- **This thread:** {thread_fb_count} feedback item{'s' if thread_fb_count != 1 else ''}"
            + (f" ({_detail})" if _detail else "")
        )
    else:
        _lines.append("- **This thread:** no feedback yet")

    if similar_fb_count > 0:
        _sims = [f["similarity"] for f in (similar_feedback or []) if f.get("similarity")]
        _avg  = f" · avg. {round(sum(_sims) / len(_sims) * 100)}% match" if _sims else ""
        _lines.append(
            f"- **Other threads:** {similar_fb_count} rated question{'s' if similar_fb_count != 1 else ''} found with similar intent{_avg}"
        )

    if lt_mem_count > 0:
        _lines.append(
            f"- **Memory:** {lt_mem_count} past interaction{'s' if lt_mem_count != 1 else ''} recalled"
        )

    if feedback_context:
        all_fb_items = (thread_feedback or []) + (similar_feedback or [])
        _fb_lines = []
        for fb in all_fb_items[:5]:
            comment  = (fb.get("comment") or "").strip()
            liked    = fb.get("liked", True)
            source   = "this thread" if fb.get("source") == "thread" else "other thread"
            qprev    = (fb.get("question_text") or "")[:60].strip()
            label    = "Keep doing" if liked else "Avoid"
            if comment:
                _fb_lines.append(f"  - **{label}** [{source}]: {comment}")
            elif qprev:
                _fb_lines.append(f"  - **{label}** [{source}]: re \"{qprev}\"")
            else:
                _fb_lines.append(f"  - **{label}** [{source}]")
        _lines.append("- **Applying:**")
        _lines.extend(_fb_lines)
    elif not _distilled_active:
        _lines.append("- **No feedback found** — no prior ratings to apply")
    preference_label = "\n".join(_lines)

    # ── Terminal visibility ────────────────────────────────────────────────────
    logger.info(
        "lt_memory_retriever DONE | thread={} | "
        "LT_MEMORY={} ({} interactions recalled) | "
        "THREAD_FB={} ({} liked / {} disliked) | "
        "SIMILAR_FB={} cross-thread | "
        "DISTILLED={} | "
        "FEEDBACK_APPLIED={}",
        thread_id,
        "LOADED" if lt_mem else "NONE", lt_mem_count,
        thread_fb_count, thread_liked, thread_disliked,
        similar_fb_count,
        "YES" if _distilled_active else "NO",
        "YES" if (feedback_context or _distilled_active) else "NO",
    )

    return {
        "lt_memory_context":  lt_mem or "",
        "feedback_context":   feedback_context,
        "preference_summary": preference_summary,
        "preference_label":   preference_label,
    }

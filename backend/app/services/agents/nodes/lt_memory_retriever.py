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


async def lt_memory_retriever(state: AnalyticsState, config: RunnableConfig) -> dict:
    user_id   = state.get("user_id") or ""
    thread_id = state.get("thread_id") or ""
    question  = state.get("question") or ""

    async def _lt_memory() -> str:
        # LT memory disabled — PostgresStore not stable; re-enable when store is confirmed healthy
        return ""

    async def _thread_fb() -> list:
        try:
            from app.db import async_session_factory
            from app.services.chat.feedback import find_thread_feedback
            async with async_session_factory() as db:
                rows = await asyncio.wait_for(
                    find_thread_feedback(db, thread_id, limit=5), timeout=2.0
                )
                return [{**r, "source": "thread"} for r in (rows or [])]
        except Exception as exc:
            logger.warning("lt_memory_retriever | thread_fb_failed | thread={} | err={}", thread_id, exc)
            return []

    async def _similar_fb() -> list:
        try:
            from app.db import async_session_factory
            from app.services.chat.feedback import find_similar_feedback
            async with async_session_factory() as db:
                rows = await asyncio.wait_for(
                    find_similar_feedback(db, question, current_thread_id=thread_id, limit=5), timeout=8.0
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

    from app.services.chat.feedback import build_feedback_context
    feedback_context = build_feedback_context(thread_feedback or [], similar_feedback or [])

    # Count LT memory items (each line is one recalled interaction)
    lt_mem_count = len([l for l in (lt_mem or "").split("\n") if l.strip()]) if lt_mem else 0

    thread_fb_count  = len(thread_feedback or [])
    similar_fb_count = len(similar_feedback or [])
    thread_liked     = sum(1 for f in (thread_feedback or []) if f.get("liked"))
    thread_disliked  = thread_fb_count - thread_liked

    # Structured summary for the about panel
    all_feedback = (thread_feedback or []) + (similar_feedback or [])
    preference_summary = {
        "long_term_memory_applied": bool(lt_mem),
        "long_term_memory_count":   lt_mem_count,
        "thread_feedback_count":    thread_fb_count,
        "similar_feedback_count":   similar_fb_count,
        "feedback_applied":         bool(feedback_context),
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

    # Markdown bullet list — rendered by MarkdownRenderer in the pipeline step UI
    if thread_fb_count > 0:
        _liked_str   = f"{thread_liked} preferred" if thread_liked > 0 else ""
        _dislike_str = f"{thread_disliked} flagged for improvement" if thread_disliked > 0 else ""
        _detail      = " · ".join(filter(None, [_liked_str, _dislike_str]))
        _lines.append(
            f"- **Conversation:** {thread_fb_count} prior rating{'s' if thread_fb_count != 1 else ''} found"
            + (f" ({_detail})" if _detail else "")
        )
    else:
        _lines.append("- **Conversation:** no prior ratings in this thread")

    if similar_fb_count > 0:
        _sims = [f["similarity"] for f in (similar_feedback or []) if f.get("similarity")]
        _avg  = f", avg. {round(sum(_sims) / len(_sims) * 100)}% semantic match" if _sims else ""
        _lines.append(
            f"- **Cross-session:** {similar_fb_count} semantically similar "
            f"quer{'ies' if similar_fb_count != 1 else 'y'} matched from other conversations{_avg}"
        )
    else:
        _lines.append("- **Cross-session:** no similar queries matched in other conversations")

    if lt_mem_count > 0:
        _lines.append(
            f"- **Memory:** {lt_mem_count} past interaction{'s' if lt_mem_count != 1 else ''} recalled from your history"
        )
    else:
        _lines.append("- **Memory:** no past interactions recalled")

    _lines.append(
        "- **Status:** feedback preferences applied to this response"
        if feedback_context
        else "- **Status:** no actionable preferences found, responding without feedback context"
    )
    preference_label = "\n".join(_lines)

    # ── Terminal visibility ────────────────────────────────────────────────────
    logger.info(
        "lt_memory_retriever DONE | thread={} | "
        "LT_MEMORY={} ({} interactions recalled) | "
        "THREAD_FB={} ({} liked / {} disliked) | "
        "SIMILAR_FB={} cross-thread | "
        "FEEDBACK_APPLIED={}",
        thread_id,
        "LOADED" if lt_mem else "NONE", lt_mem_count,
        thread_fb_count, thread_liked, thread_disliked,
        similar_fb_count,
        "YES" if feedback_context else "NO",
    )

    return {
        "lt_memory_context":  lt_mem or "",
        "feedback_context":   feedback_context,
        "preference_summary": preference_summary,
        "preference_label":   preference_label,
    }

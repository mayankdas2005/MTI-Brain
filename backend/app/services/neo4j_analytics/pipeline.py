"""Analytics pipeline streaming.

Entry point for chat.py:
    from app.services.neo4j_analytics.pipeline import stream_pipeline, cancel_stream
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.core.config import settings
from app.core.langfuse_integration import (
    create_callback_handler as _create_lf_handler,
    flush_langfuse as _lf_flush,
    langfuse_context as _lf_context,
    make_trace_public as _lf_make_public,
)
from app.core.logger import logger
from app.services.neo4j_analytics.node_names import (
    NODE_MESSAGE,
    NODE_STREAM,
    CHART_AGENT as N_CHART_AGENT,
    COMPRESS as N_COMPRESS,
    EXECUTOR as N_EXECUTOR,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.neo4j_analytics.state import AnalyticsState

_active_streams: dict[str, asyncio.Event] = {}

_STATE_KEYS = {
    "question", "question_type", "persona", "needs_clarification", "clarification_count",
    "semantic_context", "resolved_intent", "semantic_ir_list", "sql_list",
    "recompile_count", "repair_count", "filter_resolution_needed",
    "result_list", "query_summary", "no_data", "reliability_flags",
    "low_confidence_filters", "zero_row_probe_result",
    "answer", "chart_spec", "follow_ups", "error", "stopped", "prior_sql",
}


def cancel_stream(thread_id: str) -> bool:
    event = _active_streams.get(thread_id)
    if event:
        event.set()
        return True
    return False


def _build_graph_context_snapshot(state: AnalyticsState) -> dict:
    """Collect the minimal identifiers and context data needed to reconstruct the graph visualization."""
    semantic_context = state.get("semantic_context") or {}
    resolved_intent = state.get("resolved_intent") or {}
    ir_list = state.get("semantic_ir_list") or []

    path_tables: list[str] = list(dict.fromkeys(
        fqn
        for ir in ir_list
        for fqn in (ir.get("path_tables") or []) + (ir.get("anchor_tables") or [])
    ))
    join_clauses: list[str] = [
        clause
        for ir in ir_list
        for clause in (ir.get("join_clauses") or [])
    ]
    join_path_ids: list[str] = list(dict.fromkeys(
        pid
        for ir in ir_list
        for pid in (ir.get("join_path_ids") or [])
    ))
    selected_columns: list[dict] = [
        {
            "table_fqn": c.get("table_fqn") if isinstance(c, dict) else getattr(c, "table_fqn", ""),
            "column_name": c.get("column_name") if isinstance(c, dict) else getattr(c, "column_name", ""),
            "aggregation": c.get("aggregation") if isinstance(c, dict) else getattr(c, "aggregation", None),
            "alias": c.get("alias") if isinstance(c, dict) else getattr(c, "alias", None),
        }
        for ir in ir_list
        for c in ((ir.get("measures") or []) + (ir.get("dimensions") or []))
    ]

    return {
        "tables": semantic_context.get("tables") or [],
        "business_terms": semantic_context.get("business_terms") or [],
        "intents": semantic_context.get("intents") or [],
        "templates": semantic_context.get("templates") or [],
        "query_patterns": semantic_context.get("query_patterns") or [],
        "anti_patterns": semantic_context.get("anti_patterns") or [],
        "anchor_tables": resolved_intent.get("anchor_tables") or [],
        "path_tables": path_tables,
        "join_clauses": join_clauses,
        "join_path_ids": join_path_ids,
        "selected_columns": selected_columns,
        "template_id": resolved_intent.get("template_id"),
        "intent": resolved_intent.get("intent"),
    }


async def stream_pipeline(
    question: str,
    thread_id: str = "default",
    persona: str | None = None,
    user_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    feedback_context: str = "",
    user_email: str | None = None,
    user_display_name: str = "",
    max_rows: int = 100,
    **kwargs,
):
    """Run the analytics pipeline and yield SSE event dicts."""
    from app.services.neo4j_analytics.graph import get_compiled_graph, get_memory_store
    graph = get_compiled_graph()

    if cancel_event is None:
        cancel_event = asyncio.Event()
    _active_streams[thread_id] = cancel_event

    run_id = str(uuid.uuid4())[:8]
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": getattr(settings, "PIPELINE_RECURSION_LIMIT", 50),
    }

    lf_handler = _create_lf_handler()
    if lf_handler:
        config["callbacks"] = [lf_handler]

    lf_ctx = _lf_context(
        session_id=thread_id,
        user_id=user_email or user_id or user_display_name or None,
        tags=["neo4j_analytics", f"persona:{persona}"] if persona else ["neo4j_analytics"],
        metadata={"thread_id": thread_id, "run_id": run_id, "environment": settings.ENVIRONMENT},
    )

    deep_analysis: bool = bool(kwargs.get("deep_analysis", False))

    initial: AnalyticsState = {
        "messages": [],
        "user_id": user_id or "",
        "thread_id": thread_id,
        "persona": persona or "executive",
        "question": question,
        "question_type": "",
        "needs_clarification": False,
        "clarification_count": 0,
        "clarification_reason": None,
        "semantic_context": None,
        "resolved_intent": None,
        "semantic_ir_list": [],
        "sql_list": [],
        "recompile_count": 0,
        "repair_count": 0,
        "filter_resolution_needed": False,
        "result_list": [],
        "query_summary": None,
        "no_data": False,
        "reliability_flags": [],
        "low_confidence_filters": [],
        "zero_row_probe_result": None,
        "answer": "",
        "chart_spec": None,
        "follow_ups": [],
        "feedback_context": feedback_context,
        "summary": "",
        "error": None,
        "execution_error": None,
        "_prev_repair_count": -1,
        "stopped": False,
        "deep_analysis": deep_analysis,
        "max_rows": max_rows,
        "user_email": user_email,
        "pipeline_start_ms": time.perf_counter(),
        "pattern_matched": False,
        "pattern_name": None,
        "is_retry": bool(kwargs.get("is_retry", False)),
        "prior_sql": kwargs.get("prior_sql") or None,
    }

    from app.services.neo4j_analytics.helpers import MultiSectionStreamer, SectionStreamer
    from app.services.neo4j_analytics.token_tracker import NODE_TIER, aggregate_token_usage, extract_usage

    _pipeline_steps: list[dict] = []
    _step_by_visit: dict[str, int] = {}
    _visit_timers: dict[str, float] = {}
    _token_records: list[dict] = []
    _per_call_streamers: dict[str, "MultiSectionStreamer | SectionStreamer | None"] = {}
    _node_visit_count: dict[str, int] = {}
    _reasoning_entries: list[dict] = []
    _reasoning_idx: dict[str, int] = {}
    _step_reasoning_idx: dict[str, list[int]] = {}
    state: dict = {}
    pipeline_start = time.perf_counter()
    stopped = False

    def _get_streamer(call_run_id: str, node_name: str):
        s = _per_call_streamers.get(call_run_id)
        if s is not None:
            return s
        cfg = NODE_STREAM.get(node_name)
        if cfg == "multi":
            streamer = MultiSectionStreamer([("reasoning", "reasoning.delta"), ("answer", "answer.delta")])
        elif cfg:
            streamer = SectionStreamer(cfg[0])
        else:
            streamer = None
        _per_call_streamers[call_run_id] = streamer
        return streamer

    logger.info(
        "[{}] Analytics pipeline START | thread={} | user={} | user_id={} | persona={} | max_rows={} | deep={} | question={}",
        run_id, thread_id,
        user_email or user_display_name or "unknown",
        user_id or "unknown",
        persona or "executive",
        max_rows,
        deep_analysis,
        question[:120],
    )

    lf_ctx.__enter__()
    try:
        async for ev in graph.astream_events(initial, version="v2", config=config):
            if cancel_event.is_set():
                stopped = True
                yield {"event": "stopped", "data": {"message": "Stopped by user"}}
                break

            if "|" in ev.get("metadata", {}).get("langgraph_checkpoint_ns", ""):
                continue

            kind      = ev["event"]
            node      = ev.get("metadata", {}).get("langgraph_node")
            call_rid  = str(ev.get("run_id", ""))

            if kind == "on_chat_model_end":
                ai_msg = ev.get("data", {}).get("output")
                if ai_msg is not None:
                    tier = NODE_TIER.get(node or "", "balanced")
                    usage = extract_usage(ai_msg, node=node or "pipeline", tier=tier)
                    if usage:
                        _token_records.append(usage)
                        logger.debug(
                            "[{}] tokens | node={} | in={} out={} cost=${:.6f}",
                            run_id, node, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"],
                        )

            if node not in NODE_MESSAGE:
                continue

            if kind == "on_chain_start" and ev.get("name") == node:
                visit      = _node_visit_count.get(node, 0)
                visit_key  = f"{node}:{visit}"
                is_retry   = visit > 0
                t          = time.perf_counter()
                _visit_timers[visit_key] = t
                elapsed    = t - pipeline_start
                logger.info("[{}] {} START{} | +{:.1f}s", run_id, node, " (retry)" if is_retry else "", elapsed)
                _pipeline_steps.append({
                    "node":           node,
                    "message":        NODE_MESSAGE[node],
                    "status":         "active",
                    "started_at_ms":  int(elapsed * 1000),
                    "duration_ms":    None,
                    "is_retry":       is_retry,
                    "reasoning":      "",
                })
                _step_by_visit[visit_key] = len(_pipeline_steps) - 1
                yield {
                    "event": "node.start",
                    "data": {"node": node, "message": NODE_MESSAGE[node], "is_retry": is_retry},
                }
                if NODE_STREAM.get(node):
                    yield {"event": "reasoning.pending", "data": {"node": node}}

            elif kind == "on_chat_model_stream":
                if "no_stream" in ev.get("tags", []):
                    continue
                raw = ev["data"]["chunk"].content
                if isinstance(raw, list):
                    token = "".join(
                        b if isinstance(b, str) else (b.get("text", "") if isinstance(b, dict) else "")
                        for b in raw
                    )
                else:
                    token = raw if isinstance(raw, str) else ""
                if not token:
                    continue

                streamer = _get_streamer(call_rid, node)
                if not streamer:
                    continue

                visit = _node_visit_count.get(node, 0)

                def _emit_token(text: str, etype: str) -> dict:
                    if etype == "reasoning.delta":
                        if call_rid not in _reasoning_idx:
                            label = NODE_MESSAGE.get(node, node)
                            if visit > 0:
                                label += f" (attempt {visit + 1})"
                            _reasoning_idx[call_rid] = len(_reasoning_entries)
                            _reasoning_entries.append({"node": node, "label": label, "tokens": []})
                            _step_reasoning_idx.setdefault(f"{node}:{visit}", []).append(_reasoning_idx[call_rid])
                        _reasoning_entries[_reasoning_idx[call_rid]]["tokens"].append(text)
                    return {"event": etype, "data": {"node": node, "text": text}}

                if isinstance(streamer, MultiSectionStreamer):
                    text, etype = streamer.feed(token)
                    if text and etype:
                        yield _emit_token(text, etype)
                else:
                    text = streamer.feed(token)
                    if text:
                        cfg = NODE_STREAM.get(node)
                        etype = cfg[1] if isinstance(cfg, tuple) else "reasoning.delta"
                        yield _emit_token(text, etype)

            elif kind == "on_chain_end" and ev.get("name") == node:
                output = ev.get("data", {}).get("output")
                visit_before = _node_visit_count.get(node, 0)
                if isinstance(output, dict):
                    state.update({k: v for k, v in output.items() if k in _STATE_KEYS})

                _node_visit_count[node] = visit_before + 1
                visit_key  = f"{node}:{visit_before}"
                node_dur   = time.perf_counter() - _visit_timers.get(visit_key, pipeline_start)
                elapsed    = time.perf_counter() - pipeline_start
                logger.info("[{}] {} DONE | {:.1f}s | +{:.1f}s", run_id, node, node_dur, elapsed)

                if visit_key in _step_by_visit:
                    step = _pipeline_steps[_step_by_visit[visit_key]]
                    step["status"]      = "done"
                    step["duration_ms"] = round(node_dur * 1000)
                    step["reasoning"]   = "".join(
                        "".join(_reasoning_entries[i]["tokens"])
                        for i in _step_reasoning_idx.get(visit_key, [])
                    )
                    yield {
                        "event": "node.done",
                        "data": {"node": node, "duration_ms": step["duration_ms"]},
                    }

                if node == N_EXECUTOR:
                    sql_list    = state.get("sql_list") or []
                    result_list = state.get("result_list") or []
                    all_rows: list = []
                    all_cols: list = []
                    for r in result_list:
                        if r.get("rows"):
                            all_rows.extend(r["rows"])
                        if not all_cols and r.get("columns"):
                            all_cols = r["columns"]
                    yield {
                        "event": "execute.done",
                        "data": {
                            "status":        "error" if state.get("error") else "success",
                            "sql":           sql_list[0] if sql_list else "",
                            "columns":       all_cols,
                            "rows":          all_rows,
                            "row_count":     len(all_rows),
                            "will_visualize": bool(all_rows),
                        },
                    }

                elif node == N_CHART_AGENT and state.get("chart_spec"):
                    yield {"event": "chart", "data": {"spec": state["chart_spec"]}}

                elif node == N_SYNTHESIS and state.get("follow_ups"):
                    yield {"event": "follow_ups", "data": {"questions": state["follow_ups"]}}

        total       = time.perf_counter() - pipeline_start
        duration_ms = round(total * 1000)
        logger.info("[{}] Analytics pipeline DONE | {:.1f}s | stopped={}", run_id, total, stopped)

        _lf_trace_id  = lf_handler.last_trace_id if lf_handler else None
        _lf_trace_url = _lf_make_public(_lf_trace_id) if _lf_trace_id else None

        _done_rows: list = []
        _done_cols: list = []
        for _r in (state.get("result_list") or []):
            if _r.get("rows"):
                _done_rows.extend(_r["rows"])
            if not _done_cols and _r.get("columns"):
                _done_cols = _r["columns"]

        _graph_context = _build_graph_context_snapshot(state)

        try:
            yield {
                "event": "done",
                "data": {
                    "run_id":            run_id,
                    "question":          question,
                    "question_type":     state.get("question_type", "analytics"),
                    "stopped":           stopped,
                    "persona":           state.get("persona", ""),
                    "sql":               state.get("sql_list", [""])[0] if state.get("sql_list") else "",
                    "columns":           _done_cols,
                    "rows":              _done_rows,
                    "row_count":         len(_done_rows),
                    "chart_spec":        state.get("chart_spec"),
                    "answer":            state.get("answer", ""),
                    "follow_ups":        state.get("follow_ups", []),
                    "duration_ms":       duration_ms,
                    "langfuse_trace_id": _lf_trace_id,
                    "langfuse_trace_url": _lf_trace_url,
                    "pipeline_steps":    _pipeline_steps,
                    "reasoning": [
                        {
                            "node":  e["node"],
                            "label": e["label"],
                            "text":  "".join(e["tokens"]).strip(),
                        }
                        for e in _reasoning_entries
                        if "".join(e["tokens"]).strip()
                    ],
                    "no_data":           state.get("no_data", False),
                    "reliability_flags": state.get("reliability_flags", []),
                    "token_usage":       aggregate_token_usage(_token_records) if _token_records else {},
                    "graph_context":     _graph_context,
                },
            }
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.warning("[{}] Client disconnect before done: {}", run_id, e)

        memory_store = get_memory_store()
        if (
            not stopped
            and memory_store is not None
            and user_id
            and state.get("answer")
            and not state.get("no_data")
            and not state.get("error")
        ):
            try:
                from app.services.neo4j_analytics.memory.long_term import save_user_memory
                ir_list = state.get("semantic_ir_list") or []
                await save_user_memory(
                    user_id=user_id,
                    thread_id=thread_id,
                    question=question,
                    answer_summary=(state.get("answer") or "")[:500],
                    intent=ir_list[0].get("intent", "") if ir_list else "",
                    row_count=0,
                    sql=state.get("sql_list", [""])[0] if state.get("sql_list") else "",
                )
            except Exception as e:
                logger.warning("[{}] Memory save failed (non-fatal): {}", run_id, e)

    except Exception as e:
        logger.error("[{}] Analytics pipeline error: {}", run_id, e)
        yield {"event": "error", "data": {"message": "Something went wrong while processing your question. Please try again."}}
    finally:
        _active_streams.pop(thread_id, None)
        try:
            lf_ctx.__exit__(None, None, None)
        except Exception:
            pass
        _lf_flush()

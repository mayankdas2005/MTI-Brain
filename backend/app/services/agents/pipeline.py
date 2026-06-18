"""Analytics pipeline streaming.

Entry point for chat.py:
    from app.services.agents.pipeline import stream_pipeline, cancel_stream
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
from app.services.agents.node_names import (
    NODE_MESSAGE,
    NODE_STREAM,
    CHART_AGENT as N_CHART_AGENT,
    COMPRESS as N_COMPRESS,
    ERROR_RESPONSE as N_ERROR_RESPONSE,
    EXECUTOR as N_EXECUTOR,
    LT_MEMORY_RETRIEVER as N_LT_MEMORY_RETRIEVER,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.agents.nodes.audit import write_query_pattern, write_schema_gaps
from app.services.agents.neo4j.template_search import find_canonical_pattern_id
from app.services.agents.nodes.confidence import compute_confidence
from app.services.agents.helpers import format_sql
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState

_active_streams: dict[str, asyncio.Event] = {}

_STATE_KEYS = {
    "question", "question_type", "persona", "needs_clarification", "clarification_count",
    "current_date",
    "semantic_context", "enriched_schema", "anchor_tables_resolved", "resolved_intent",
    "intent_directive", "intent_directive_instructions", "intent_directive_context",
    "filter_directive", "schema_directive",
    "semantic_ir_list", "sql_list",
    "recompile_count", "repair_count", "filter_resolution_needed",
    "result_list", "query_summary", "no_data", "reliability_flags",
    "low_confidence_filters", "zero_row_probe_result", "zero_row_rewrite_count",
    "data_quality_flag", "data_quality_reason",
    "answer", "chart_spec", "chart_type", "alternative_chart_specs", "follow_ups", "error", "stopped", "prior_sql",
    "query_intent", "entity_tokens", "search_terms", "is_followup", "complexity",
    "preference_summary", "neo4j_raw_graph",
    "_measure_specialist_output", "_dimension_specialist_output", "_directive_summary", "_cte_outline",
}


def cancel_stream(thread_id: str) -> bool:
    event = _active_streams.get(thread_id)
    if event:
        event.set()
        return True
    return False


def _build_graph_context_snapshot(state: AnalyticsState) -> dict:
    """Build the minimal snapshot needed to reconstruct the trust graph visualization.

    Shows only what agents ACTUALLY used:
    - anchor_tables / path_tables from SemanticIR (tables in the SQL, not all candidates)
    - used_columns: measures + dimensions + filters + time_filter with role annotations
    - join_clauses / join_path_ids: only the paths used, not all candidates
    - business_terms, intents, query_patterns, anti_patterns from semantic_context
    - is_cross_domain / cross_domain_hub for cross-domain queries
    """
    semantic_context = state.get("semantic_context") or {}
    resolved_intent = state.get("resolved_intent") or {}
    ir_list = state.get("semantic_ir_list") or []

    # Tables that appear in the SQL — NOT all 8+ candidate tables from context fetcher
    anchor_tables: list[str] = list(dict.fromkeys(
        fqn
        for ir in ir_list
        for fqn in (ir.get("anchor_tables") or [])
    ))
    path_tables: list[str] = list(dict.fromkeys(
        fqn
        for ir in ir_list
        for fqn in (ir.get("path_tables") or []) + (ir.get("anchor_tables") or [])
    ))

    # Only join clauses actually used in SQL (skip empty strings = unresolved pairs)
    join_clauses: list[str] = [
        clause
        for ir in ir_list
        for clause in (ir.get("join_clauses") or [])
        if clause
    ]
    # Only JoinPath IDs from the IR itself — not all candidate paths
    join_path_ids: list[str] = list(dict.fromkeys(
        pid
        for ir in ir_list
        for pid in (ir.get("join_path_ids") or [])
        if pid
    ))

    # Columns actually referenced in SQL: measures + dimensions + filters + time_filter
    # Role annotation lets the visualization distinguish how each column was used
    used_columns: list[dict] = []
    seen_cols: set[tuple] = set()

    def _add_col(table_fqn: str, col_name: str, role: str, agg: str | None = None) -> None:
        key = (table_fqn, col_name)
        if not table_fqn or not col_name or key in seen_cols:
            return
        seen_cols.add(key)
        entry: dict = {"table_fqn": table_fqn, "column_name": col_name, "role": role}
        if agg:
            entry["aggregation"] = agg
        used_columns.append(entry)

    def _get(obj, key: str, default=""):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    for ir in ir_list:
        for m in (ir.get("measures") or []):
            _add_col(_get(m, "table_fqn"), _get(m, "column_name"), "measure", _get(m, "aggregation") or None)
        for d in (ir.get("dimensions") or []):
            _add_col(_get(d, "table_fqn"), _get(d, "column_name"), "dimension")
        for f in (ir.get("filters") or []):
            _add_col(_get(f, "table_fqn"), _get(f, "column_name"), "filter")
        tf = ir.get("time_filter")
        if tf:
            col_name = _get(tf, "column_name") or _get(tf, "column")
            _add_col(_get(tf, "table_fqn"), col_name, "time_filter")

    return {
        "anchor_tables": anchor_tables,
        "path_tables": path_tables,
        "join_clauses": join_clauses,
        "join_path_ids": join_path_ids,
        "used_columns": used_columns,
        "business_terms": semantic_context.get("business_terms") or [],
        "intents": semantic_context.get("intents") or [],
        "query_patterns": semantic_context.get("query_patterns") or [],
        "anti_patterns": semantic_context.get("anti_patterns") or [],
        "templates": semantic_context.get("templates") or [],
        "template_id": resolved_intent.get("template_id", ""),
        "is_cross_domain": semantic_context.get("is_cross_domain", False),
        "cross_domain_hub": semantic_context.get("cross_domain_hub"),
        "intent": resolved_intent.get("intent"),
        # REMOVED: "tables" (all candidates — too broad),
        #          "selected_columns" (replaced by "used_columns" which includes filters)
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
    from app.services.agents.graph import get_compiled_graph, get_memory_store
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
        tags=["agents", f"persona:{persona}"] if persona else ["agents"],
        metadata={"thread_id": thread_id, "run_id": run_id, "environment": settings.ENVIRONMENT},
    )

    deep_analysis: bool = bool(kwargs.get("deep_analysis", False))

    _prior_sql = kwargs.get("prior_sql") or ""
    _prior_sql_tables: list[str] = []
    if _prior_sql:
        try:
            import sqlglot
            stmt = sqlglot.parse_one(_prior_sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE)
            if stmt:
                _prior_sql_tables = list({
                    f"{t.db}.{t.name}"
                    for t in stmt.find_all(sqlglot.exp.Table)
                    if t.db and t.name
                })
        except Exception:
            pass

    initial: AnalyticsState = {
        "messages": [],
        "user_id": user_id or "",
        "thread_id": thread_id,
        "persona": persona or "analyst",
        "question": question,
        "question_type": "",
        "needs_clarification": False,
        "clarification_count": 0,
        "clarification_reason": None,
        "semantic_context": None,
        "resolved_intent": None,
        "intent_directive": "",
        "intent_directive_instructions": "",
        "intent_directive_context": "",
        "filter_directive": "",
        "schema_directive": "",
        "semantic_ir_list": [],
        "sql_list": [],
        "enriched_schema": None,
        "anchor_tables_resolved": [],
        "specialist_outputs": [],
        "data_quality_flag": False,
        "data_quality_reason": None,
        "recompile_count": 0,
        "repair_count": 0,
        "repair_history": [],
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
        "lt_memory_context": "",
        "preference_summary": None,
        "summary": "",
        "error": None,
        "execution_error": None,
        "_prev_repair_count": -1,
        "stopped": False,
        "deep_analysis": deep_analysis,
        "tribal_facts": [],
        "max_rows": max_rows,
        "user_email": user_email,
        "current_date": time.strftime("%Y-%m-%d"),
        "pipeline_start_ms": time.perf_counter(),
        "pattern_matched": False,
        "pattern_name": None,
        "is_retry": bool(kwargs.get("is_retry", False)),
        "prior_sql": kwargs.get("prior_sql") or None,
        "prior_question": kwargs.get("prior_question") or None,
        "prior_sql_tables": _prior_sql_tables,
        "is_refinement": bool(_prior_sql) and not bool(kwargs.get("is_retry")),
    }

    from app.services.agents.helpers import MultiSectionStreamer, SectionStreamer
    from app.services.agents.token_tracker import NODE_TIER, aggregate_token_usage, extract_usage

    _pipeline_steps: list[dict] = []
    _step_by_visit: dict[str, int] = {}
    _visit_timers: dict[str, float] = {}
    _token_records: list[dict] = []
    _per_call_streamers: dict[str, "MultiSectionStreamer | SectionStreamer | None"] = {}
    _node_visit_count: dict[str, int] = {}
    _reasoning_entries: list[dict] = []
    _reasoning_idx: dict[str, int] = {}
    _step_reasoning_idx: dict[str, list[int]] = {}
    state: dict = dict(initial)
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
        persona or "analyst",
        max_rows,
        deep_analysis,
        question[:120],
    )

    lf_ctx.__enter__()
    cancel_task: asyncio.Task | None = None
    try:
        aiter = graph.astream_events(initial, version="v2", config=config).__aiter__()
        cancel_task = asyncio.create_task(cancel_event.wait())
        while True:
            next_task = asyncio.create_task(aiter.__anext__())
            done, _ = await asyncio.wait(
                {next_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_event.is_set():
                next_task.cancel()
                try:
                    await next_task
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                stopped = True
                yield {"event": "stopped", "data": {"message": "Stopped by user"}}
                break
            try:
                ev = next_task.result()
            except StopAsyncIteration:
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

                # Emit a synthetic reasoning.delta for deterministic nodes that have a
                # natural language label to show in the UI pipeline timeline.
                _preference_label = ""
                if node == N_LT_MEMORY_RETRIEVER and isinstance(output, dict):
                    _preference_label = output.get("preference_label") or ""
                    if _preference_label:
                        yield {"event": "reasoning.delta", "data": {"node": node, "text": _preference_label}}

                _node_visit_count[node] = visit_before + 1
                visit_key  = f"{node}:{visit_before}"
                node_dur   = time.perf_counter() - _visit_timers.get(visit_key, pipeline_start)
                elapsed    = time.perf_counter() - pipeline_start
                logger.info("[{}] {} DONE | {:.1f}s | +{:.1f}s", run_id, node, node_dur, elapsed)

                if visit_key in _step_by_visit:
                    step = _pipeline_steps[_step_by_visit[visit_key]]
                    # A node that wrote error into state has failed even if it completed cleanly.
                    # Mark it error immediately so the frontend doesn't flash a blue checkmark
                    # before error_response arrives.
                    node_set_error = (
                        node == N_ERROR_RESPONSE
                        or (isinstance(output, dict) and bool(output.get("error")))
                    )
                    step["status"]      = "error" if node_set_error else "done"
                    step["duration_ms"] = round(node_dur * 1000)
                    llm_reasoning = "".join(
                        "".join(_reasoning_entries[i]["tokens"])
                        for i in _step_reasoning_idx.get(visit_key, [])
                    )
                    # For deterministic nodes with a synthetic label (e.g. lt_memory_retriever),
                    # persist the label as step reasoning so it survives reload.
                    step["reasoning"] = llm_reasoning or _preference_label
                    yield {
                        "event": "node.done",
                        "data": {
                            "node":        node,
                            "duration_ms": step["duration_ms"],
                            "status":      step["status"],
                        },
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
                            "sql":           format_sql(sql_list[0]) if sql_list else "",
                            "columns":       all_cols,
                            "rows":          all_rows,
                            "row_count":     len(all_rows),
                            "will_visualize": bool(all_rows),
                        },
                    }

                elif node == N_CHART_AGENT and state.get("chart_spec"):
                    yield {"event": "chart", "data": {
                        "spec": state["chart_spec"],
                        "chart_type": state.get("chart_type"),
                        "alternative_chart_specs": state.get("alternative_chart_specs", []),
                    }}

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

        _qs = state.get("query_summary") or {}
        _sample_rows = [list(r.values()) for r in (_qs.get("sample_rows") or [])]
        _query_col_stats = [
            {
                "name":           c.get("name"),
                "dtype":          c.get("dtype"),
                "distinct_count": c.get("distinct_count"),
                "min":            c.get("min"),
                "max":            c.get("max"),
                "mean":           c.get("mean"),
                "null_count":     c.get("null_count"),
                "total_count":    c.get("total_count"),
                "top_values":     c.get("top_values"),
            }
            for c in (_qs.get("columns") or [])
        ]
        _was_truncated = bool(_qs.get("was_truncated", False))
        _true_total_rows = _qs.get("true_total_rows")
        logger.info(
            "[{}] done event | rows={} | sample_rows={} | was_truncated={} | true_total_rows={}",
            run_id[:8], len(_done_rows), len(_sample_rows), _was_truncated, _true_total_rows,
        )

        _confidence = await compute_confidence({
            **state,
            "question": question,
            "_rows": _done_rows,
            "_cols": _done_cols,
        }) if not stopped else None
        if _confidence:
            logger.info("[{}] confidence | score={} | label={}", run_id[:8], _confidence.get("score"), _confidence.get("label"))
            yield {"event": "confidence", "data": _confidence}

        # ── Loop 1: write QueryPattern (confidence-gated) + SchemaGaps ─────────
        _confidence_score = _confidence.get("score", 0) if _confidence else 0
        _pattern_id: str | None = None
        if not stopped and _done_rows and _confidence_score >= 60:
            _ir_list = state.get("semantic_ir_list", [])
            _first_ir = SemanticIR(**_ir_list[0]) if _ir_list else None
            _sql = state.get("sql_list", [""])[0] if state.get("sql_list") else ""
            if _first_ir and _sql:
                # Dedup: find canonical pattern id BEFORE emitting "done" so
                # PostgreSQL stores the right id and feedback hits the canonical node.
                _embedding = (state.get("semantic_context") or {}).get("query_embedding") or []
                _intent = (state.get("resolved_intent") or {}).get("intent", "")
                _tables = list((state.get("resolved_intent") or {}).get("anchor_tables") or [])
                _existing_id: str | None = None
                if _embedding:
                    try:
                        _existing_id = await asyncio.to_thread(
                            find_canonical_pattern_id, _embedding, _intent, _tables
                        )
                    except Exception as _dedup_err:
                        logger.warning("[{}] pattern dedup failed, creating fresh node | {}", run_id[:8], _dedup_err)
                _pattern_id = _existing_id or str(uuid.uuid4())
                asyncio.create_task(
                    write_query_pattern(state, _sql, _first_ir, _confidence_score, _pattern_id, is_update=bool(_existing_id))
                )
        asyncio.create_task(write_schema_gaps(state))

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
                    "tables_used":       (state.get("semantic_ir_list") or [{}])[0].get("anchor_tables") or [],
                    # "intent":            (state.get("semantic_ir_list") or [{}])[0].get("intent") or "",
                    "intent":            "\n\n".join(
                        f"**{line.split(': ', 1)[0]}**: {line.split(': ', 1)[1]}" if ": " in line else line
                        for line in state["query_intent"]
                    ) if isinstance(state.get("query_intent"), list) else (state.get("query_intent") or ""),

                    "complexity":        (state.get("semantic_ir_list") or [{}])[0].get("complexity") or "",
                    "columns":           _done_cols,
                    "rows":              _done_rows,
                    "row_count":         len(_done_rows),
                    "sample_rows":       _sample_rows,
                    "query_col_stats":   _query_col_stats,
                    "was_truncated":     _was_truncated,
                    "true_total_rows":   _true_total_rows,
                    "chart_spec":        state.get("chart_spec"),
                    "chart_type":        state.get("chart_type"),
                    "alternative_chart_specs": state.get("alternative_chart_specs", []),
                    "answer":            state.get("answer", ""),
                    "follow_ups":        state.get("follow_ups", []),
                    "confidence":        _confidence,
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
                    "neo4j_raw_graph":   state.get("neo4j_raw_graph") or {"nodes": [], "edges": []},
                    "pattern_id":        _pattern_id,
                    "query_intent":      state.get("query_intent") or [],
                    "entity_tokens":     state.get("entity_tokens") or [],
                    "search_terms":      state.get("search_terms") or [],
                    "is_followup":       state.get("is_followup", False),
                    "preference_summary": state.get("preference_summary"),
                },
            }
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.warning("[{}] Client disconnect before done: {}", run_id, e)

        # LT memory save disabled — PostgresStore not stable; re-enable when store is confirmed healthy
        # memory_store = get_memory_store()
        # if not stopped and memory_store and user_id and state.get("answer") ...
        pass

    except Exception as e:
        logger.error("[{}] Analytics pipeline error: {}", run_id, e)
        yield {"event": "error", "data": {"message": "Something went wrong while processing your question. Please try again."}}
    finally:
        _active_streams.pop(thread_id, None)
        # Clean up the cancel wait task if it's still pending
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            try:
                await cancel_task
            except asyncio.CancelledError:
                pass
        try:
            lf_ctx.__exit__(None, None, None)
        except Exception:
            pass
        _lf_flush()

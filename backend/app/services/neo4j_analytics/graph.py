"""LangGraph graph wiring for the Neo4j analytics pipeline.

10-node graph: intake → context_fetcher → intent_resolver → query_compiler
               → filter_resolver → sql_validator → executor → synthesis → chart_agent

All routing logic lives here. Nodes return state updates; routing functions
read state and return next node name.

Entry point for chat.py:
    from app.services.neo4j_analytics.graph import stream_pipeline
"""

from __future__ import annotations

import asyncio
import time
import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.langfuse_integration import (
    create_callback_handler as _create_lf_handler,
    flush_langfuse as _lf_flush,
    langfuse_context as _lf_context,
    make_trace_public as _lf_make_public,
)
from app.core.logger import logger
from app.services.neo4j_analytics import neo4j_client, redis_client
from app.services.neo4j_analytics.memory import long_term as lt_memory
from app.services.neo4j_analytics.nodes.chart_agent import chart_agent
from app.services.neo4j_analytics.nodes.clarification import clarification
from app.services.neo4j_analytics.nodes.context_fetcher import context_fetcher
from app.services.neo4j_analytics.nodes.error_response import error_response
from app.services.neo4j_analytics.nodes.executor import executor
from app.services.neo4j_analytics.nodes.filter_resolver import filter_resolver
from app.services.neo4j_analytics.nodes.general_chat import general_chat
from app.services.neo4j_analytics.nodes.intake_classifier import intake_classifier
from app.services.neo4j_analytics.nodes.intent_resolver import intent_resolver
from app.services.neo4j_analytics.nodes.query_compiler import query_compiler
from app.services.neo4j_analytics.nodes.sql_validator import sql_validator
from app.services.neo4j_analytics.nodes.compress import compress, SUMMARIZE_THRESHOLD
from app.services.neo4j_analytics.nodes.synthesis import synthesis
from app.services.neo4j_analytics.node_names import (
    NODE_MESSAGE,
    NODE_STREAM,
    CHART_AGENT as N_CHART_AGENT,
    CLARIFICATION as N_CLARIFICATION,
    COMPRESS as N_COMPRESS,
    CONTEXT_FETCHER as N_CONTEXT_FETCHER,
    ERROR_RESPONSE as N_ERROR_RESPONSE,
    EXECUTOR as N_EXECUTOR,
    FILTER_RESOLVER as N_FILTER_RESOLVER,
    GENERAL_CHAT as N_GENERAL_CHAT,
    INTAKE as N_INTAKE,
    INTENT_RESOLVER as N_INTENT_RESOLVER,
    QUERY_COMPILER as N_QUERY_COMPILER,
    SQL_VALIDATOR as N_SQL_VALIDATOR,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.neo4j_analytics.state import AnalyticsState

MAX_RECOMPILE = 1
MAX_REPAIR = 2
MAX_CLARIFICATION = 2
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)

_checkpoint_pool: AsyncConnectionPool | None = None
_compiled_graph = None
_memory_store = None
_memory_store_exit = None
_active_streams: dict[str, asyncio.Event] = {}

# ─── Routing functions ────────────────────────────────────────────────────────

def route_intake(state: AnalyticsState) -> str:
    qt = state.get("question_type", "analytics")
    if qt == "general_chat":
        logger.info("route: intake → general_chat | thread={}", state["thread_id"])
        return N_GENERAL_CHAT
    logger.info("route: intake → context_fetcher | thread={}", state["thread_id"])
    return N_CONTEXT_FETCHER


def route_after_context_fetcher(state: AnalyticsState) -> str:
    if state.get("error") == "semantic_layer_unavailable":
        logger.info("route: context_fetcher → error_response | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    logger.info("route: context_fetcher → intent_resolver | thread={}", state["thread_id"])
    return N_INTENT_RESOLVER


def route_intent(state: AnalyticsState) -> str:
    logger.info("route: intent_resolver → query_compiler | thread={}", state["thread_id"])
    return N_QUERY_COMPILER


def route_after_clarification(state: AnalyticsState) -> str:
    logger.info("route: clarification → intent_resolver | thread={}", state["thread_id"])
    return N_INTENT_RESOLVER


def route_compiler(state: AnalyticsState) -> str:
    if state.get("filter_resolution_needed"):
        logger.info("route: query_compiler → filter_resolver | thread={}", state["thread_id"])
        return N_FILTER_RESOLVER
    logger.info("route: query_compiler → sql_validator | thread={}", state["thread_id"])
    return N_SQL_VALIDATOR


def route_filter_resolver(state: AnalyticsState) -> str:
    logger.info("route: filter_resolver → sql_validator | thread={}", state["thread_id"])
    return N_SQL_VALIDATOR


def route_validator(state: AnalyticsState) -> str:
    recompile_count = state.get("recompile_count", 0)
    if state.get("error"):
        if recompile_count < MAX_RECOMPILE:
            logger.info("route: sql_validator → query_compiler (recompile={}) | thread={}", recompile_count + 1, state["thread_id"])
            return N_QUERY_COMPILER
        logger.info("route: sql_validator → error_response (max recompiles) | thread={}", state["thread_id"])
        return N_ERROR_RESPONSE
    logger.info("route: sql_validator → executor | thread={}", state["thread_id"])
    return N_EXECUTOR


def route_executor(state: AnalyticsState) -> str:
    repair_count = state.get("repair_count", 0)
    recompile_count = state.get("recompile_count", 0)
    clarification_count = state.get("clarification_count", 0)

    if state.get("stopped"):
        logger.info("route: executor → synthesis (stopped) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if state.get("error") and repair_count >= MAX_REPAIR:
        if recompile_count < MAX_RECOMPILE:
            logger.info("route: executor → intent_resolver (repairs exhausted, retry with error context) | thread={}", state["thread_id"])
            return N_INTENT_RESOLVER
        logger.info("route: executor → synthesis (repairs + recompiles exhausted) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if repair_count > state.get("_prev_repair_count", -1):
        logger.info("route: executor → sql_validator (after repair) | thread={}", state["thread_id"])
        return N_SQL_VALIDATOR

    if state.get("needs_clarification") and clarification_count < MAX_CLARIFICATION:
        logger.info("route: executor → clarification | thread={}", state["thread_id"])
        return N_CLARIFICATION

    logger.info("route: executor → synthesis | thread={}", state["thread_id"])
    return N_SYNTHESIS


def route_synthesis(state: AnalyticsState) -> str:
    result_list = state.get("result_list") or []
    has_rows = any(r.get("rows") for r in result_list)
    if has_rows and not state.get("no_data"):
        logger.info("route: synthesis → chart_agent | thread={}", state["thread_id"])
        return N_CHART_AGENT
    logger.info("route: synthesis → compress_check (no data for chart) | thread={}", state["thread_id"])
    return N_COMPRESS if _should_compress(state) else END


def _should_compress(state: AnalyticsState) -> bool:
    return len(state.get("messages") or []) >= SUMMARIZE_THRESHOLD


def route_should_compress(state: AnalyticsState) -> str:
    if _should_compress(state):
        logger.info("route: → compress | thread={} | messages={}", state["thread_id"], len(state.get("messages") or []))
        return N_COMPRESS
    return END


# ─── Graph builder ────────────────────────────────────────────────────────────

def compile_graph():
    """Build and compile the analytics LangGraph."""
    b = StateGraph(AnalyticsState)

    b.add_node(N_INTAKE,          intake_classifier, retry_policy=LLM_RETRY)
    b.add_node(N_GENERAL_CHAT,    general_chat,      retry_policy=LLM_RETRY)
    b.add_node(N_CONTEXT_FETCHER, context_fetcher)
    b.add_node(N_INTENT_RESOLVER, intent_resolver,   retry_policy=LLM_RETRY)
    b.add_node(N_CLARIFICATION,   clarification,     retry_policy=LLM_RETRY)
    b.add_node(N_QUERY_COMPILER,  query_compiler,    retry_policy=LLM_RETRY)
    b.add_node(N_FILTER_RESOLVER, filter_resolver)
    b.add_node(N_SQL_VALIDATOR,   sql_validator)
    b.add_node(N_EXECUTOR,        executor,          retry_policy=LLM_RETRY)
    b.add_node(N_SYNTHESIS,       synthesis,         retry_policy=LLM_RETRY)
    b.add_node(N_CHART_AGENT,     chart_agent,       retry_policy=LLM_RETRY)
    b.add_node(N_ERROR_RESPONSE,  error_response)
    b.add_node(N_COMPRESS,        compress,          retry_policy=LLM_RETRY)

    b.add_edge(START, N_INTAKE)

    b.add_conditional_edges(
        N_INTAKE,
        route_intake,
        {N_GENERAL_CHAT: N_GENERAL_CHAT, N_CONTEXT_FETCHER: N_CONTEXT_FETCHER},
    )

    b.add_conditional_edges(N_GENERAL_CHAT, route_should_compress, {N_COMPRESS: N_COMPRESS, END: END})

    b.add_conditional_edges(
        N_CONTEXT_FETCHER,
        route_after_context_fetcher,
        {N_ERROR_RESPONSE: N_ERROR_RESPONSE, N_INTENT_RESOLVER: N_INTENT_RESOLVER},
    )

    b.add_conditional_edges(
        N_INTENT_RESOLVER,
        route_intent,
        {N_CLARIFICATION: N_CLARIFICATION, N_QUERY_COMPILER: N_QUERY_COMPILER},
    )

    b.add_conditional_edges(
        N_CLARIFICATION,
        route_after_clarification,
        {N_INTENT_RESOLVER: N_INTENT_RESOLVER},
    )

    b.add_conditional_edges(
        N_QUERY_COMPILER,
        route_compiler,
        {N_FILTER_RESOLVER: N_FILTER_RESOLVER, N_SQL_VALIDATOR: N_SQL_VALIDATOR},
    )

    b.add_conditional_edges(
        N_FILTER_RESOLVER,
        route_filter_resolver,
        {N_CLARIFICATION: N_CLARIFICATION, N_SQL_VALIDATOR: N_SQL_VALIDATOR},
    )

    b.add_conditional_edges(
        N_SQL_VALIDATOR,
        route_validator,
        {N_QUERY_COMPILER: N_QUERY_COMPILER, N_EXECUTOR: N_EXECUTOR, N_ERROR_RESPONSE: N_ERROR_RESPONSE},
    )

    b.add_conditional_edges(
        N_EXECUTOR,
        route_executor,
        {
            N_SQL_VALIDATOR: N_SQL_VALIDATOR,
            N_CLARIFICATION: N_CLARIFICATION,
            N_SYNTHESIS: N_SYNTHESIS,
            N_INTENT_RESOLVER: N_INTENT_RESOLVER,
        },
    )

    b.add_conditional_edges(
        N_SYNTHESIS,
        route_synthesis,
        {N_CHART_AGENT: N_CHART_AGENT, END: END},
    )

    b.add_conditional_edges(N_CHART_AGENT, route_should_compress, {N_COMPRESS: N_COMPRESS, END: END})
    b.add_conditional_edges(N_ERROR_RESPONSE, route_should_compress, {N_COMPRESS: N_COMPRESS, END: END})
    b.add_edge(N_COMPRESS, END)

    return b


def get_compiled_graph():
    """Return the singleton compiled graph (lazy-init)."""
    global _compiled_graph
    if _compiled_graph is None:
        raise RuntimeError("Analytics pipeline not initialized — call init_analytics_pipeline() first.")
    return _compiled_graph


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_analytics_pipeline() -> None:
    """Initialize Neo4j, Redis, Redshift, checkpoint store, and compile graph."""
    global _checkpoint_pool, _compiled_graph, _memory_store, _memory_store_exit

    neo4j_client.init_neo4j()
    redis_client.init_redis()

    try:
        from app.services.neo4j_analytics.bedrock import init_llms
        init_llms()
    except Exception as e:
        logger.warning("LLM init failed (non-fatal — will init on first use): {}", e)

    try:
        from app.services.neo4j_analytics.redshift_client import init_redshift
        await init_redshift()
    except Exception as e:
        logger.warning("Redshift init failed (non-fatal for startup): {}", e)

    conninfo = settings.CHECKPOINT_CONNINFO
    conninfo_fast = conninfo + " connect_timeout=10"

    checkpointer = None
    try:
        async with await AsyncConnection.connect(
            conninfo_fast, autocommit=True, prepare_threshold=0
        ) as conn:
            try:
                await AsyncPostgresSaver(conn).setup()
            except UniqueViolation:
                pass

        _checkpoint_pool = AsyncConnectionPool(
            conninfo=conninfo_fast,
            open=False,
            min_size=settings.CHECKPOINT_POOL_MIN,
            max_size=settings.CHECKPOINT_POOL_MAX,
            check=AsyncConnectionPool.check_connection,
            max_idle=settings.CHECKPOINT_POOL_MAX_IDLE,
            kwargs={"prepare_threshold": 0},
        )
        await _checkpoint_pool.open()
        checkpointer = AsyncPostgresSaver(_checkpoint_pool)
        logger.info("Analytics checkpoint store initialized")
    except Exception as e:
        logger.warning("Checkpoint store init failed (non-fatal — running without persistence): {}", e)
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    try:
        from contextlib import ExitStack
        from langgraph.store.postgres import PostgresStore
        from langgraph.store.base import IndexConfig
        from app.services.embeddings import embed_texts_sync
        _memory_store_exit = ExitStack()
        _memory_store = _memory_store_exit.enter_context(
            PostgresStore.from_conn_string(
                conninfo_fast,
                index=IndexConfig(embed=embed_texts_sync, dims=1536),
            )
        )
        _memory_store.setup()
        lt_memory.set_memory_store(_memory_store)
        logger.info("Analytics memory store initialized")
    except Exception as e:
        logger.warning("Analytics memory store init failed (non-fatal): {}", e)
        _memory_store = None

    _compiled_graph = compile_graph().compile(checkpointer=checkpointer, store=_memory_store)

    logger.info("Neo4j analytics pipeline initialized")


async def shutdown_analytics_pipeline() -> None:
    """Shut down all analytics pipeline resources."""
    global _checkpoint_pool, _memory_store_exit

    try:
        from app.services.neo4j_analytics.redshift_client import close_redshift
        await close_redshift()
    except Exception:
        pass

    neo4j_client.close_neo4j()
    redis_client.close_redis()

    if _memory_store_exit:
        try:
            _memory_store_exit.close()
        except Exception:
            pass
        _memory_store_exit = None

    pool, _checkpoint_pool = _checkpoint_pool, None
    if pool:
        try:
            await pool.close(timeout=3.0)
        except Exception:
            logger.warning("Analytics checkpoint pool close failed")

    logger.info("Neo4j analytics pipeline shut down")


def cancel_stream(thread_id: str) -> bool:
    event = _active_streams.get(thread_id)
    if event:
        event.set()
        return True
    return False


# NODE_MESSAGE and NODE_STREAM are imported from node_names — single source of truth.
# Nodes absent from NODE_MESSAGE are completely hidden from the UI (e.g. compress).

_STATE_KEYS = {
    "question", "question_type", "persona", "needs_clarification", "clarification_count",
    "semantic_context", "resolved_intent", "semantic_ir_list", "sql_list",
    "recompile_count", "repair_count", "filter_resolution_needed",
    "result_list", "query_summary", "no_data", "reliability_flags",
    "low_confidence_filters", "zero_row_probe_result",
    "answer", "chart_spec", "follow_ups", "error", "stopped",
    "decompose_needed",
}

# ─── Streaming ────────────────────────────────────────────────────────────────

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
        "decompose_needed": False,
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
    }

    from app.services.neo4j_analytics.helpers import MultiSectionStreamer, SectionStreamer
    from app.services.neo4j_analytics.token_tracker import NODE_TIER, aggregate_token_usage, extract_usage

    _pipeline_steps: list[dict] = []
    _step_by_visit: dict[str, int] = {}        # "node:visit" → index in _pipeline_steps
    _visit_timers: dict[str, float] = {}       # "node:visit" → wall-clock start
    _token_records: list[dict] = []
    _per_call_streamers: dict[str, "MultiSectionStreamer | SectionStreamer | None"] = {}
    _node_visit_count: dict[str, int] = {}
    _reasoning_entries: list[dict] = []        # {node, label, tokens[]}
    _reasoning_idx: dict[str, int] = {}        # call_run_id → index in _reasoning_entries
    _step_reasoning_idx: dict[str, list[int]] = {}  # "node:visit" → list of reasoning entry indices
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
        "[{}] Analytics pipeline START | thread={} | persona={} | question={}",
        run_id, thread_id, persona, question[:80],
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

            # Token tracking runs before display filtering so all LLM calls are costed.
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
                    "columns":           [],
                    "rows":              [],
                    "row_count":         0,
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
                },
            }
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.warning("[{}] Client disconnect before done: {}", run_id, e)

        if (
            not stopped
            and _memory_store is not None
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

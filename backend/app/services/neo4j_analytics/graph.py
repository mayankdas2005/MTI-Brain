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
from app.services.neo4j_analytics.nodes.synthesis import synthesis
from app.services.neo4j_analytics.node_names import (
    CHART_AGENT as N_CHART_AGENT,
    CLARIFICATION as N_CLARIFICATION,
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
    count = state.get("clarification_count", 0)
    if state.get("needs_clarification") and count < MAX_CLARIFICATION:
        logger.info("route: intent_resolver → clarification | thread={} | count={}", state["thread_id"], count)
        return N_CLARIFICATION
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
    count = state.get("clarification_count", 0)
    if state.get("needs_clarification") and count < MAX_CLARIFICATION:
        logger.info("route: filter_resolver → clarification | thread={}", state["thread_id"])
        return N_CLARIFICATION
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
    clarification_count = state.get("clarification_count", 0)

    if state.get("stopped"):
        logger.info("route: executor → synthesis (stopped) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if state.get("error") and repair_count >= MAX_REPAIR:
        logger.info("route: executor → synthesis (max repairs) | thread={}", state["thread_id"])
        return N_SYNTHESIS

    if state.get("repair_count", 0) > (state.get("_prev_repair_count", -1)):
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
    logger.info("route: synthesis → END (no data for chart) | thread={}", state["thread_id"])
    return END


# ─── Graph builder ────────────────────────────────────────────────────────────

def compile_graph():
    """Build and compile the analytics LangGraph."""
    b = StateGraph(AnalyticsState)

    b.add_node(N_INTAKE, intake_classifier)
    b.add_node(N_GENERAL_CHAT, general_chat)
    b.add_node(N_CONTEXT_FETCHER, context_fetcher)
    b.add_node(N_INTENT_RESOLVER, intent_resolver)
    b.add_node(N_CLARIFICATION, clarification)
    b.add_node(N_QUERY_COMPILER, query_compiler)
    b.add_node(N_FILTER_RESOLVER, filter_resolver)
    b.add_node(N_SQL_VALIDATOR, sql_validator)
    b.add_node(N_EXECUTOR, executor)
    b.add_node(N_SYNTHESIS, synthesis)
    b.add_node(N_CHART_AGENT, chart_agent)
    b.add_node(N_ERROR_RESPONSE, error_response)

    b.add_edge(START, N_INTAKE)

    b.add_conditional_edges(
        N_INTAKE,
        route_intake,
        {N_GENERAL_CHAT: N_GENERAL_CHAT, N_CONTEXT_FETCHER: N_CONTEXT_FETCHER},
    )

    b.add_edge(N_GENERAL_CHAT, END)

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
        },
    )

    b.add_conditional_edges(
        N_SYNTHESIS,
        route_synthesis,
        {N_CHART_AGENT: N_CHART_AGENT, END: END},
    )

    b.add_edge(N_CHART_AGENT, END)
    b.add_edge(N_ERROR_RESPONSE, END)

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
    async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
        try:
            await AsyncPostgresSaver(conn).setup()
        except UniqueViolation:
            pass

    _checkpoint_pool = AsyncConnectionPool(
        conninfo=conninfo,
        open=False,
        min_size=1,
        max_size=5,
        check=AsyncConnectionPool.check_connection,
        max_idle=300,
    )
    await _checkpoint_pool.open()

    try:
        from contextlib import ExitStack
        from langgraph.store.postgres import PostgresStore
        from langgraph.store.base import IndexConfig
        from app.services.embeddings import embed_texts_sync
        _memory_store_exit = ExitStack()
        _memory_store = _memory_store_exit.enter_context(
            PostgresStore.from_conn_string(
                conninfo,
                index=IndexConfig(embed=embed_texts_sync, dims=1536),
            )
        )
        _memory_store.setup()
        lt_memory.set_memory_store(_memory_store)
        logger.info("Analytics memory store initialized")
    except Exception as e:
        logger.warning("Analytics memory store init failed (non-fatal): {}", e)
        _memory_store = None

    checkpointer = AsyncPostgresSaver(_checkpoint_pool)
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


# ─── Node → SSE display config ───────────────────────────────────────────────

_NODE_MESSAGE = {
    N_INTAKE: "Understanding your question",
    N_GENERAL_CHAT: "Responding",
    N_CONTEXT_FETCHER: "Loading data catalog",
    N_INTENT_RESOLVER: "Interpreting your question",
    N_CLARIFICATION: "Requesting clarification",
    N_QUERY_COMPILER: "Building query",
    N_FILTER_RESOLVER: "Resolving filter values",
    N_SQL_VALIDATOR: "Validating SQL",
    N_EXECUTOR: "Querying data warehouse",
    N_SYNTHESIS: "Preparing your answer",
    N_CHART_AGENT: "Building chart",
    N_ERROR_RESPONSE: "Error occurred",
}

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
        "persona": persona or "analyst",
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
        "stopped": False,
        "deep_analysis": deep_analysis,
    }

    from app.services.neo4j_analytics.helpers import MultiSectionStreamer
    from app.services.neo4j_analytics.token_tracker import NODE_TIER, aggregate_token_usage, extract_usage

    _pipeline_steps: list[dict] = []
    _step_timers: dict[str, float] = {}
    _token_records: list[dict] = []
    _node_streamers: dict[str, MultiSectionStreamer] = {}
    state: dict = {}
    pipeline_start = time.perf_counter()
    stopped = False

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

            kind = ev["event"]
            node = ev.get("metadata", {}).get("langgraph_node")

            if node not in _NODE_MESSAGE:
                continue

            if kind == "on_chain_start":
                _step_timers[node] = time.perf_counter()
                elapsed = time.perf_counter() - pipeline_start
                logger.info("[{}] {} START | +{:.1f}s", run_id, node, elapsed)
                _pipeline_steps.append({
                    "node": node,
                    "message": _NODE_MESSAGE[node],
                    "status": "active",
                    "started_at_ms": int(elapsed * 1000),
                    "duration_ms": None,
                })
                _node_streamers[node] = MultiSectionStreamer([
                    ("reasoning", "reasoning.delta"),
                    ("answer", "answer.delta"),
                ])
                yield {
                    "event": "node.start",
                    "data": {"node": node, "message": _NODE_MESSAGE[node]},
                }
                yield {"event": "reasoning.pending", "data": {"node": node}}

            elif kind == "on_chat_model_stream":
                if "no_stream" in ev.get("tags", []):
                    continue
                raw_content = ev["data"]["chunk"].content
                token = raw_content if isinstance(raw_content, str) else ""
                if token and node in _node_streamers:
                    emitted, event_type = _node_streamers[node].feed(token)
                    if emitted and event_type:
                        yield {"event": event_type, "data": {"node": node, "text": emitted}}

            elif kind == "on_chat_model_end":
                ai_message = ev.get("data", {}).get("output")
                if ai_message is not None:
                    tier = NODE_TIER.get(node, "balanced")
                    usage = extract_usage(ai_message, node=node, tier=tier)
                    if usage:
                        _token_records.append(usage)
                        logger.debug(
                            "[{}] tokens | node={} | in={} out={} cost=${:.6f}",
                            run_id, node, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"],
                        )

            elif kind == "on_chain_end":
                output = ev.get("data", {}).get("output")
                if isinstance(output, dict):
                    state.update({k: v for k, v in output.items() if k in _STATE_KEYS})

                node_dur = time.perf_counter() - _step_timers.get(node, pipeline_start)
                elapsed = time.perf_counter() - pipeline_start
                logger.info("[{}] {} DONE | {:.1f}s | +{:.1f}s", run_id, node, node_dur, elapsed)

                for step in _pipeline_steps:
                    if step["node"] == node and step["status"] == "active":
                        step["status"] = "done"
                        step["duration_ms"] = round(node_dur * 1000)
                        break

                yield {
                    "event": "node.done",
                    "data": {"node": node, "duration_ms": round(node_dur * 1000)},
                }

                if node == N_EXECUTOR:
                    ir_list = state.get("semantic_ir_list") or []
                    sql_list = state.get("sql_list") or []
                    result_list = state.get("result_list") or []
                    all_rows = []
                    all_cols = []
                    for r in result_list:
                        if r.get("rows"):
                            all_rows.extend(r["rows"])
                        if not all_cols and r.get("columns"):
                            all_cols = r["columns"]
                    yield {
                        "event": "execute.done",
                        "data": {
                            "status": "error" if state.get("error") else "success",
                            "sql": sql_list[0] if sql_list else "",
                            "columns": all_cols,
                            "rows": all_rows,
                            "row_count": len(all_rows),
                            "will_visualize": bool(all_rows),
                        },
                    }

                elif node == N_CHART_AGENT and state.get("chart_spec"):
                    yield {"event": "chart", "data": {"spec": state["chart_spec"]}}

                elif node == N_SYNTHESIS and state.get("follow_ups"):
                    yield {"event": "follow_ups", "data": {"questions": state["follow_ups"]}}

        total = time.perf_counter() - pipeline_start
        duration_ms = round(total * 1000)
        logger.info("[{}] Analytics pipeline DONE | {:.1f}s | stopped={}", run_id, total, stopped)

        _lf_trace_id = lf_handler.last_trace_id if lf_handler else None
        _lf_trace_url = _lf_make_public(_lf_trace_id) if _lf_trace_id else None

        try:
            yield {
                "event": "done",
                "data": {
                    "run_id": run_id,
                    "question": question,
                    "question_type": state.get("question_type", "analytics"),
                    "stopped": stopped,
                    "persona": state.get("persona", ""),
                    "sql": state.get("sql_list", [""])[0] if state.get("sql_list") else "",
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "chart_spec": state.get("chart_spec"),
                    "answer": state.get("answer", ""),
                    "follow_ups": state.get("follow_ups", []),
                    "duration_ms": duration_ms,
                    "langfuse_trace_id": _lf_trace_id,
                    "langfuse_trace_url": _lf_trace_url,
                    "pipeline_steps": _pipeline_steps,
                    "no_data": state.get("no_data", False),
                    "reliability_flags": state.get("reliability_flags", []),
                    "token_usage": aggregate_token_usage(_token_records) if _token_records else {},
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

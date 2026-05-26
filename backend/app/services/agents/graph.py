"""LangGraph pipeline graph construction and SSE streaming for MTI Brain.

Two compiled graphs:
  _inner_graph — starts at domain_specialist; used by executor_node for each sub-Q
  _main_graph  — starts at intake_classify; routes simple/complex to inner pipeline
                 or advanced to outer Plan/Execute/Reflect/Repair loop

Both share the same AsyncPostgresSaver checkpoint pool (mirroring quest exactly).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from app.core.langfuse_integration import (
    create_callback_handler as _create_lf_handler,
    flush_langfuse as _lf_flush,
    langfuse_context as _lf_context,
    make_trace_public as _lf_make_public,
)


from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logger import logger
from app.services.agents import data_pool as dp
from app.services.agents.node_names import N
from backend.app.services.neo4j_analytics.bedrock import init_llms
from backend.app.services.neo4j_analytics.helpers import MultiSectionStreamer, SectionStreamer
from backend.app.services.neo4j_analytics.token_tracker import (
    NODE_TIER,
    aggregate_token_usage,
    extract_usage,
)
from app.services.agents.nodes.brain import brain_retrieval_node
from app.services.agents.nodes.compress import compress_node
from app.services.agents.nodes.domain import domain_specialist_node
from app.services.agents.nodes.executor import executor_node
from app.services.agents.nodes.final_reflector import final_reflector_node
from app.services.agents.nodes.governance import governance_gate_node
from app.services.agents.nodes.graph_reasoning import graph_reasoning_node
from app.services.agents.nodes.human_loop import human_in_loop_node
from app.services.agents.nodes.intake import (
    general_chat_node,
    intake_classify_node,
    rejected_node,
)
from app.services.agents.nodes.ontology import ontology_lookup_node
from app.services.agents.nodes.plan import plan_node
from app.services.agents.nodes.plan_validator import plan_validator_node
from app.services.agents.nodes.repairer import repairer_node
from app.services.agents.nodes.sparql_execute import sparql_execute_node
from app.services.agents.nodes.sparql_gen import sparql_gen_node
from app.services.agents.nodes.sparql_validate import sparql_validate_node
from app.services.agents.nodes.step_reflector import step_reflector_node
from app.services.agents.nodes.synthesis import answer_synthesis_node
from app.services.agents.nodes.verifier import verifier_node
from app.services.agents.nodes.visualization import visualization_node
from app.services.agents.ontology_loader import init_ontology
from app.services.agents.state import State

MAX_SPARQL_RETRIES = 2
SUMMARIZE_THRESHOLD = 6
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)

# Human-readable display labels for intent keys sent to the frontend.
# Internal pipeline code always uses the raw snake_case keys.
INTENT_DISPLAY_LABELS: dict[str, str] = {
    # Treasury
    "balance_lookup":           "Balance Lookup",
    "exposure_analysis":        "Exposure Analysis",
    "investment_and_maturity":  "Investment & Maturity",
    # Payments
    "authorization_analysis":   "Authorization Analysis",
    "cost_and_fee_analysis":    "Cost & Fee Analysis",
    "payment_operations":       "Payment Operations",
    "supplier_and_crossborder": "Supplier & Cross-Border",
    # Strategic
    "trend_and_forecast":       "Trend & Forecast",
    "code_lookup":              "Code Lookup",
    "general_analytics":        "General Analytics",
    # Legacy keys
    "counterparty_exposure":    "Counterparty Exposure",
    "fx_exposure":              "FX Exposure",
    "investment_positions":     "Investment Positions",
    "maturity_ladder":          "Maturity Ladder",
    "policy_check":             "Policy Check",
    "trend_analysis":           "Trend Analysis",
    "scenario_forecast":        "Scenario Forecast",
    "multi_entity_join":        "Multi-Entity Analysis",
}

_checkpoint_pool: AsyncConnectionPool | None = None
_main_graph = None
_inner_graph = None
_memory_store = None
_memory_store_exit = None   # holds ExitStack to keep PostgresStore context alive

_active_streams: dict[str, asyncio.Event] = {}


# ─── Routing functions ────────────────────────────────────────────────────────

def route_after_intake(state: State) -> str:
    qt = state.get("question_type", "kg_query")
    if qt == "general_chat":
        return N.GENERAL_CHAT
    if qt == "rejected":
        return N.REJECTED
    complexity = state.get("complexity", "simple")
    if complexity == "advanced":
        return N.PLAN
    return N.DOMAIN_SPECIALIST


def route_after_validate(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return N.SPARQL_GEN
        return N.ANSWER_SYNTHESIS
    return N.GOVERNANCE_GATE


def route_after_governance(state: State) -> str:
    if state.get("governance_halt"):
        return END
    return N.SPARQL_EXECUTE


def route_after_execute(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return N.SPARQL_GEN
        return N.ANSWER_SYNTHESIS
    if state.get("kg_row_count", 0) == 0:
        return N.VERIFIER  # skip graph_reasoning on empty results — verifier decides retry vs accept
    return N.GRAPH_REASONING


def route_after_verifier(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return N.SPARQL_GEN
    return N.ANSWER_SYNTHESIS


def route_after_hil(state: State) -> str:
    return N.COMPRESS


def route_after_synthesis(state: State) -> str:
    if len(state.get("messages", [])) >= SUMMARIZE_THRESHOLD:
        return N.COMPRESS
    return END


def route_synthesis_to_viz(state: State) -> str:
    return N.VISUALIZATION if len(state.get("kg_rows", [])) > 1 else N.SKIP_VIZ


def route_after_general_chat(state: State) -> str:
    if len(state.get("messages", [])) >= SUMMARIZE_THRESHOLD:
        return N.COMPRESS
    return END


def route_after_plan_validator(state: State) -> str:
    halt = state.get("halt_reason")
    if halt:
        plan_attempts = state.get("plan_attempts", 0)
        if plan_attempts < 2:
            return N.PLAN
        return END
    return N.EXECUTOR


def route_after_step_reflector(state: State) -> str:
    scratchpad = state.get("scratchpad", {})
    any_fail = any(
        r.get("status") == "needs_repair"
        for r in scratchpad.values()
    )
    if any_fail:
        return N.REPAIRER
    return N.FINAL_REFLECTOR


def route_after_repairer(state: State) -> str:
    halt = state.get("halt_reason")
    if halt:
        plan_attempts = state.get("plan_attempts", 0)
        if plan_attempts < 2:
            return N.PLAN
        return N.FINAL_REFLECTOR
    sub_questions = state.get("sub_questions", [])
    has_pending = any(sq["status"] == "pending" for sq in sub_questions)
    if has_pending:
        return N.EXECUTOR
    return N.FINAL_REFLECTOR


# ─── Graph builders ───────────────────────────────────────────────────────────

def _build_inner_graph() -> StateGraph:
    """Inner pipeline: domain_specialist → visualization (12 nodes).

    Used by executor_node to run each sub-Q independently.
    No intake_classify; starts directly at domain_specialist.
    """
    b = StateGraph(State)

    b.add_node(N.DOMAIN_SPECIALIST, domain_specialist_node, retry_policy=LLM_RETRY)
    b.add_node(N.ONTOLOGY_LOOKUP,   ontology_lookup_node)
    b.add_node(N.BRAIN_RETRIEVAL,   brain_retrieval_node)
    b.add_node(N.SPARQL_GEN,        sparql_gen_node, retry_policy=LLM_RETRY)
    b.add_node(N.SPARQL_VALIDATE,   sparql_validate_node)
    b.add_node(N.GOVERNANCE_GATE,   governance_gate_node)
    b.add_node(N.SPARQL_EXECUTE,    sparql_execute_node)
    b.add_node(N.GRAPH_REASONING,   graph_reasoning_node, retry_policy=LLM_RETRY)
    b.add_node(N.VERIFIER,          verifier_node)
    b.add_node(N.ANSWER_SYNTHESIS,  answer_synthesis_node, retry_policy=LLM_RETRY)
    b.add_node(N.VISUALIZATION,     visualization_node)

    b.add_edge(START, N.DOMAIN_SPECIALIST)
    b.add_edge(N.DOMAIN_SPECIALIST, N.ONTOLOGY_LOOKUP)
    b.add_edge(N.ONTOLOGY_LOOKUP,   N.BRAIN_RETRIEVAL)
    b.add_edge(N.BRAIN_RETRIEVAL,   N.SPARQL_GEN)
    b.add_edge(N.SPARQL_GEN,        N.SPARQL_VALIDATE)
    b.add_conditional_edges(
        N.SPARQL_VALIDATE,
        route_after_validate,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.GOVERNANCE_GATE: N.GOVERNANCE_GATE, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )
    b.add_conditional_edges(
        N.GOVERNANCE_GATE,
        route_after_governance,
        {N.SPARQL_EXECUTE: N.SPARQL_EXECUTE, END: END},
    )
    b.add_conditional_edges(
        N.SPARQL_EXECUTE,
        route_after_execute,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.GRAPH_REASONING: N.GRAPH_REASONING, N.VERIFIER: N.VERIFIER, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )
    b.add_edge(N.GRAPH_REASONING, N.VERIFIER)
    b.add_conditional_edges(
        N.VERIFIER,
        route_after_verifier,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )
    b.add_conditional_edges(
        N.ANSWER_SYNTHESIS,
        route_synthesis_to_viz,
        {N.VISUALIZATION: N.VISUALIZATION, N.SKIP_VIZ: END},
    )
    b.add_edge(N.VISUALIZATION, END)

    return b


def _build_main_graph() -> StateGraph:
    """Main graph: intake_classify → routes to inner pipeline or outer loop."""
    b = StateGraph(State)

    # ── All nodes ──
    b.add_node(N.INTAKE_CLASSIFY,   intake_classify_node, retry_policy=LLM_RETRY)
    b.add_node(N.GENERAL_CHAT,      general_chat_node, retry_policy=LLM_RETRY)
    b.add_node(N.REJECTED,          rejected_node)

    # Inner pipeline nodes
    b.add_node(N.DOMAIN_SPECIALIST, domain_specialist_node, retry_policy=LLM_RETRY)
    b.add_node(N.ONTOLOGY_LOOKUP,   ontology_lookup_node)
    b.add_node(N.BRAIN_RETRIEVAL,   brain_retrieval_node)
    b.add_node(N.SPARQL_GEN,        sparql_gen_node, retry_policy=LLM_RETRY)
    b.add_node(N.SPARQL_VALIDATE,   sparql_validate_node)
    b.add_node(N.GOVERNANCE_GATE,   governance_gate_node)
    b.add_node(N.SPARQL_EXECUTE,    sparql_execute_node)
    b.add_node(N.GRAPH_REASONING,   graph_reasoning_node, retry_policy=LLM_RETRY)
    b.add_node(N.VERIFIER,          verifier_node)

    # Outer loop nodes
    b.add_node(N.PLAN,           plan_node, retry_policy=LLM_RETRY)
    b.add_node(N.PLAN_VALIDATOR, plan_validator_node)
    b.add_node(N.EXECUTOR,       executor_node)
    b.add_node(N.STEP_REFLECTOR, step_reflector_node, retry_policy=LLM_RETRY)
    b.add_node(N.REPAIRER,       repairer_node)
    b.add_node(N.FINAL_REFLECTOR,final_reflector_node, retry_policy=LLM_RETRY)

    # Convergence nodes (shared by inner + outer paths)
    b.add_node(N.ANSWER_SYNTHESIS, answer_synthesis_node, retry_policy=LLM_RETRY)
    b.add_node(N.VISUALIZATION,    visualization_node)
    b.add_node(N.HUMAN_IN_LOOP,    human_in_loop_node)
    b.add_node(N.COMPRESS,         compress_node, retry_policy=LLM_RETRY)

    # ── Edges ──
    b.add_edge(START, N.INTAKE_CLASSIFY)
    b.add_conditional_edges(
        N.INTAKE_CLASSIFY,
        route_after_intake,
        {
            N.GENERAL_CHAT:      N.GENERAL_CHAT,
            N.REJECTED:          N.REJECTED,
            N.DOMAIN_SPECIALIST: N.DOMAIN_SPECIALIST,
            N.PLAN:              N.PLAN,
        },
    )
    b.add_edge(N.REJECTED, END)
    b.add_conditional_edges(
        N.GENERAL_CHAT,
        route_after_general_chat,
        {N.COMPRESS: N.COMPRESS, END: END},
    )

    # ── Inner pipeline path (simple / complex) ──
    b.add_edge(N.DOMAIN_SPECIALIST, N.ONTOLOGY_LOOKUP)
    b.add_edge(N.ONTOLOGY_LOOKUP,   N.BRAIN_RETRIEVAL)
    b.add_edge(N.BRAIN_RETRIEVAL,   N.SPARQL_GEN)
    b.add_edge(N.SPARQL_GEN,        N.SPARQL_VALIDATE)
    b.add_conditional_edges(
        N.SPARQL_VALIDATE,
        route_after_validate,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.GOVERNANCE_GATE: N.GOVERNANCE_GATE, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )
    b.add_conditional_edges(
        N.GOVERNANCE_GATE,
        route_after_governance,
        {N.SPARQL_EXECUTE: N.SPARQL_EXECUTE, END: END},
    )
    b.add_conditional_edges(
        N.SPARQL_EXECUTE,
        route_after_execute,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.GRAPH_REASONING: N.GRAPH_REASONING, N.VERIFIER: N.VERIFIER, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )
    b.add_edge(N.GRAPH_REASONING, N.VERIFIER)
    b.add_conditional_edges(
        N.VERIFIER,
        route_after_verifier,
        {N.SPARQL_GEN: N.SPARQL_GEN, N.ANSWER_SYNTHESIS: N.ANSWER_SYNTHESIS},
    )

    # ── Outer loop path (advanced) ──
    b.add_conditional_edges(
        N.PLAN,
        lambda s: N.PLAN_VALIDATOR,
        {N.PLAN_VALIDATOR: N.PLAN_VALIDATOR},
    )
    b.add_conditional_edges(
        N.PLAN_VALIDATOR,
        route_after_plan_validator,
        {N.PLAN: N.PLAN, N.EXECUTOR: N.EXECUTOR, END: END},
    )
    b.add_edge(N.EXECUTOR, N.STEP_REFLECTOR)
    b.add_conditional_edges(
        N.STEP_REFLECTOR,
        route_after_step_reflector,
        {N.REPAIRER: N.REPAIRER, N.FINAL_REFLECTOR: N.FINAL_REFLECTOR},
    )
    b.add_conditional_edges(
        N.REPAIRER,
        route_after_repairer,
        {N.PLAN: N.PLAN, N.EXECUTOR: N.EXECUTOR, N.FINAL_REFLECTOR: N.FINAL_REFLECTOR},
    )
    b.add_edge(N.FINAL_REFLECTOR, N.ANSWER_SYNTHESIS)

    # ── Convergence: both paths → answer_synthesis → visualization → HIL → compress → END ──
    b.add_conditional_edges(
        N.ANSWER_SYNTHESIS,
        route_synthesis_to_viz,
        {N.VISUALIZATION: N.VISUALIZATION, N.SKIP_VIZ: N.HUMAN_IN_LOOP},
    )
    b.add_edge(N.VISUALIZATION, N.HUMAN_IN_LOOP)
    b.add_conditional_edges(
        N.HUMAN_IN_LOOP,
        route_after_hil,
        {N.COMPRESS: N.COMPRESS, END: END},
    )
    b.add_edge(N.COMPRESS, END)

    return b


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_pipeline() -> None:
    """Initialize LLMs, data pool, ontology, checkpoint store, memory store, and compile graphs."""
    global _checkpoint_pool, _main_graph, _inner_graph, _memory_store, _memory_store_exit

    init_llms()
    init_ontology()
    await dp.init_data_pool()

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
            max_size=settings.CHECKPOINT_POOL_MAX * 2,
            check=AsyncConnectionPool.check_connection,
            max_idle=settings.CHECKPOINT_POOL_MAX_IDLE,
            kwargs={
                "prepare_threshold": 0,
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        )
        await _checkpoint_pool.open()
        checkpointer = AsyncPostgresSaver(_checkpoint_pool)
        logger.info("Checkpoint store initialized")
    except Exception as e:
        logger.warning(f"Checkpoint store init failed (non-fatal — running without persistence): {e}")
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    # ── Long-term memory store (cross-thread episodic memory) ──
    # PostgresStore.from_conn_string() returns a context manager — enter it and
    # keep the connection alive for the app lifetime via ExitStack.
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
        logger.info("Memory store initialized")
    except Exception as e:
        logger.warning(f"Memory store initialization failed (continuing without it): {e}")
        _memory_store = None
        if _memory_store_exit:
            _memory_store_exit.close()
            _memory_store_exit = None

    _main_graph = _build_main_graph().compile(checkpointer=checkpointer, store=_memory_store)
    _inner_graph = _build_inner_graph().compile(store=_memory_store)

    logger.info("MTI Brain pipeline initialized")


async def shutdown_pipeline() -> None:
    """Close data pool, checkpoint pool, and memory store. Caller enforces the hard timeout."""
    global _checkpoint_pool, _memory_store_exit
    if _memory_store_exit:
        try:
            _memory_store_exit.close()
        except Exception:
            pass
        _memory_store_exit = None
    try:
        await dp.close_data_pool()
    except Exception:
        logger.warning("Data pool close failed")
    pool, _checkpoint_pool = _checkpoint_pool, None
    if pool:
        try:
            await pool.close(timeout=3.0)
        except Exception:
            logger.warning("Checkpoint pool close failed")
    logger.info("MTI Brain pipeline shut down")


def get_inner_graph():
    """Return the compiled inner graph for use by executor_node."""
    if not _inner_graph:
        raise RuntimeError("Pipeline not initialized — call init_pipeline() first.")
    return _inner_graph


def cancel_stream(thread_id: str) -> bool:
    event = _active_streams.get(thread_id)
    if event:
        event.set()
        return True
    return False


# ─── Node → SSE stream config ─────────────────────────────────────────────────

# Nodes absent from _NODE_MESSAGE are completely hidden — no node.start event,
# no pipeline step, nothing reaches the frontend or DB.
# To hide a node: remove it from _NODE_MESSAGE (and _NODE_STREAM).
#
# Nodes in _NO_REASONING_NODES are shown as steps but their reasoning text
# is stripped before being sent to the frontend / saved to DB.
# To suppress reasoning for a node: add its name here.
_NO_REASONING_NODES: set[str] = set()

_NODE_STREAM = {
    N.INTAKE_CLASSIFY:   ("reasoning", "reasoning.delta"),
    N.DOMAIN_SPECIALIST: ("reasoning", "reasoning.delta"),
    N.SPARQL_GEN:        ("reasoning", "reasoning.delta"),
    N.GRAPH_REASONING:   ("reasoning", "reasoning.delta"),
    N.ANSWER_SYNTHESIS:  "multi",
    N.PLAN:              ("reasoning", "reasoning.delta"),
    N.STEP_REFLECTOR:    None,
    N.FINAL_REFLECTOR:   ("reasoning", "reasoning.delta"),
    N.GENERAL_CHAT:      ("answer", "answer.delta"),
    N.ONTOLOGY_LOOKUP:   ("reasoning", "reasoning.delta"),
    N.BRAIN_RETRIEVAL:   ("reasoning", "reasoning.delta"),
    N.SPARQL_VALIDATE:   ("reasoning", "reasoning.delta"),
    N.SPARQL_EXECUTE:    None,
    N.GOVERNANCE_GATE:   None,
    N.VERIFIER:          None,
    N.VISUALIZATION:     ("reasoning", "reasoning.delta"),
    N.PLAN_VALIDATOR:    None,
    N.EXECUTOR:          ("reasoning", "reasoning.delta"),
    N.REPAIRER:          None,
    N.REJECTED:          None,
}

_NODE_MESSAGE = {
    N.INTAKE_CLASSIFY:   "Understanding your question",
    N.GENERAL_CHAT:      "Responding",
    N.REJECTED:          "Request not supported",
    N.DOMAIN_SPECIALIST: "Identifying data scope",
    N.ONTOLOGY_LOOKUP:   "Resolving ontology terms",
    N.BRAIN_RETRIEVAL:   "Retrieving policy context",
    N.SPARQL_GEN:        "Generating SPARQL query",
    N.SPARQL_VALIDATE:   "Validating query",
    N.GOVERNANCE_GATE:   "Governance check",
    N.SPARQL_EXECUTE:    "Querying Knowledge Graph",
    N.GRAPH_REASONING:   "Analyzing results",
    N.ANSWER_SYNTHESIS:  "Preparing your answer",
    N.VISUALIZATION:     "Building chart",
    N.PLAN:              "Planning sub-questions",
    N.PLAN_VALIDATOR:    "Validating plan",
    N.EXECUTOR:          "Executing sub-questions",
    N.STEP_REFLECTOR:    "Reflecting on sub-results",
    N.REPAIRER:          "Repairing failed sub-queries",
    N.FINAL_REFLECTOR:   "Final quality check",
}

_STATE_KEYS = {
    "question", "question_type", "persona", "complexity", "intent", "routing",
    "sparql", "sparql_error", "sparql_retries",
    "kg_columns", "kg_rows", "kg_row_count",
    "answer", "chart_json", "viz_spec", "follow_ups",
    "hil_required", "hil_approved", "governance_halt",
    "pipeline_steps", "summary", "messages",
    "plan", "sub_questions", "scratchpad", "plan_attempts",
    "budget_used", "halt_reason", "final_reflection",
}


# ─── Streaming ────────────────────────────────────────────────────────────────

async def stream_pipeline(
    question: str,
    thread_id: str = "default",
    persona: str | None = None,
    user_id: str | None = None,
    max_rows: int = 100,
    deep_analysis: bool = False,
    cancel_event: asyncio.Event | None = None,
    feedback_context: str = "",
    prior_sql: str = "",
    user_email: str | None = None,
    user_display_name: str = "",
):
    """Run the pipeline and yield SSE event dicts as processing progresses."""
    if _main_graph is None:
        raise RuntimeError("Pipeline not initialized — call init_pipeline() first.")

    if cancel_event is None:
        cancel_event = asyncio.Event()
        _active_streams[thread_id] = cancel_event
    run_id = str(uuid.uuid4())[:8]

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.PIPELINE_RECURSION_LIMIT,
    }

    lf_handler = _create_lf_handler()
    callbacks: list = []
    if lf_handler:
        callbacks.append(lf_handler)
    if callbacks:
        config["callbacks"] = callbacks

    lf_ctx = _lf_context(
        session_id=thread_id,
        user_id=user_email or user_id or user_display_name or None,
        tags=[f"persona:{persona}"] if persona else [],
        metadata={
            "thread_id": thread_id,
            "run_id": run_id,
            "environment": settings.ENVIRONMENT,
        },
    )


    initial = {
        "question": question,
        "question_type": "",
        "persona": persona or "",
        "complexity": "",
        "intent": "",
        "routing": "",
        "ontology_terms": [],
        "prior_sql": prior_sql,
        "sparql": "",
        "sparql_error": "",
        "sparql_retries": 0,
        "kg_results": [],
        "kg_columns": [],
        "kg_rows": [],
        "kg_row_count": 0,
        "tribal_facts": [],
        "reasoning": "",
        "evidence": [],
        "answer": "",
        "chart_json": None,
        "viz_spec": None,
        "follow_ups": [],
        "hil_required": False,
        "hil_approved": None,
        "governance_halt": None,
        "pipeline_steps": [],
        "messages": [],
        # NOTE: "summary" is intentionally absent — LangGraph restores the
        # checkpointed value so compress_node output survives across questions
        # in the same thread. Setting it here would wipe thread memory every turn.
        "cross_thread_context": "",
        "feedback_context": feedback_context,
        "stopped": False,
        "max_rows": max_rows,
        "deep_analysis": deep_analysis,
        "_thread_id": thread_id,
        "_user_id": user_id or "",
        "plan": {},
        "sub_questions": [],
        "scratchpad": {},
        "plan_attempts": 0,
        "budget_used": {},
        "halt_reason": None,
        "final_reflection": "",
    }

    _token_usage_records: list[dict] = []
    _token_tracked_run_ids: set[str] = set()

    per_call_streamers: dict[str, SectionStreamer | MultiSectionStreamer | None] = {}

    def _ensure_streamer(call_run_id: str, node_name: str):
        s = per_call_streamers.get(call_run_id)
        if s is not None:
            return s
        c = _NODE_STREAM.get(node_name)
        if c == "multi":
            per_call_streamers[call_run_id] = MultiSectionStreamer([
                ("reasoning", "reasoning.delta"),
                ("answer", "answer.delta"),
            ])
        elif c:
            per_call_streamers[call_run_id] = SectionStreamer(c[0])
        else:
            per_call_streamers[call_run_id] = None
        return per_call_streamers[call_run_id]

    node_started: set = set()
    node_first_seen: set = set()
    node_visit_count: dict[str, int] = {}
    node_timers: dict[str, float] = {}
    state: dict = {}
    reasoning_entries: list[dict] = []
    _reasoning_idx: dict[str, int] = {}
    _pipeline_steps: list[dict] = []
    _step_by_visit: dict[str, int] = {}       # "node:visit" -> index in _pipeline_steps
    _visit_timers: dict[str, float] = {}      # "node:visit" -> wall-clock start
    _step_reasoning_idx: dict[str, list[int]] = {}  # "node:visit" -> reasoning_entries indices
    pipeline_start = time.perf_counter()
    stopped = False

    # Tracks run_ids of real outer executor node starts.
    # Used to filter inner sub-graph events that bubble up to the outer stream
    # even when ainvoke is used (LangGraph propagates via contextvars callbacks).
    _executor_run_ids: set[str] = set()
    # Nodes that exist in the inner sub-graph (run inside executor for sub-questions).
    # Events for these nodes are filtered while any real executor is active.
    _INNER_GRAPH_NODES: set[str] = {
        N.DOMAIN_SPECIALIST, N.ONTOLOGY_LOOKUP, N.BRAIN_RETRIEVAL,
        N.SPARQL_GEN, N.SPARQL_VALIDATE, N.GOVERNANCE_GATE,
        N.SPARQL_EXECUTE, N.GRAPH_REASONING, N.VERIFIER,
        N.ANSWER_SYNTHESIS, N.VISUALIZATION,
    }

    logger.info(
        f"[{run_id}] Pipeline START | thread={thread_id} | "
        f"deep_analysis={deep_analysis} | persona={persona!r} | max_rows={max_rows} | "
        f"question={question[:80]!r}"
    )

    lf_ctx.__enter__()
    try:
        async for ev in _main_graph.astream_events(initial, version="v2", config=config):
            if cancel_event.is_set():
                stopped = True
                yield {"event": "stopped", "data": {"message": "Stopped by user"}}
                break

            # Skip events with proper LangGraph sub-graph namespace prefix
            if "|" in ev.get("metadata", {}).get("langgraph_checkpoint_ns", ""):
                continue

            kind = ev["event"]
            run_id_str = str(ev.get("run_id", ""))
            node = ev.get("metadata", {}).get("langgraph_node")

            # Record real executor node starts (before any filtering)
            if kind == "on_chain_start" and node == N.EXECUTOR:
                _executor_run_ids.add(run_id_str)

            # ── Token tracking — captured before display-layer filtering so
            # sub-question LLM calls (inner graph) are included in cost totals.
            if kind == "on_chat_model_end" and run_id_str not in _token_tracked_run_ids:
                _token_tracked_run_ids.add(run_id_str)
                _out = ev.get("data", {}).get("output")
                _msg = (
                    _out if hasattr(_out, "usage_metadata") else
                    (_out[-1] if isinstance(_out, list) and _out else None)
                )
                if _msg is not None:
                    _tier = NODE_TIER.get(node or "", "balanced")
                    _usage = extract_usage(_msg, node or "pipeline", _tier)
                    if _usage:
                        _token_usage_records.append(_usage)

            # Filter events for inner sub-graph nodes while executor is active.
            # These are sub-question runs — their events must not surface at the outer level.
            if _executor_run_ids and node in _INNER_GRAPH_NODES:
                continue

            # Filter spurious executor on_chain_end events caused by inner graph
            # completions leaking as executor ends (different run_id than the real executor).
            if kind == "on_chain_end" and node == N.EXECUTOR:
                if run_id_str not in _executor_run_ids:
                    continue
                _executor_run_ids.discard(run_id_str)

            if node not in _NODE_MESSAGE:
                continue

            if kind == "on_chain_start":
                visit_key = f"{node}:{node_visit_count.get(node, 0)}"
                if visit_key in node_started:
                    continue
                node_started.add(visit_key)
                is_retry_node = node in node_first_seen
                node_first_seen.add(node)
                node_timers[node] = time.perf_counter()
                _visit_timers[visit_key] = node_timers[node]
                _pipeline_steps.append({
                    "node": node,
                    "message": _NODE_MESSAGE[node],
                    "status": "active",
                    "started_at_ms": int((node_timers[node] - pipeline_start) * 1000),
                    "duration_ms": None,
                    "reasoning": "",
                    "is_retry": is_retry_node,
                })
                _step_by_visit[visit_key] = len(_pipeline_steps) - 1
                elapsed = time.perf_counter() - pipeline_start
                logger.info(f"[{run_id}] {node} START{' (retry)' if is_retry_node else ''} | +{elapsed:.1f}s")
                yield {
                    "event": "node.start",
                    "data": {
                        "node": node,
                        "message": _NODE_MESSAGE[node],
                        "is_retry": is_retry_node,
                    },
                }
                if _NODE_STREAM.get(node):
                    yield {"event": "reasoning.pending", "data": {"node": node}}

            elif kind == "on_chat_model_stream":
                if "no_stream" in ev.get("tags", []):
                    continue
                call_run_id = str(ev.get("run_id", ""))
                s = _ensure_streamer(call_run_id, node)
                if not s:
                    continue
                raw_content = ev["data"]["chunk"].content
                if isinstance(raw_content, list):
                    parts: list[str] = []
                    for block in raw_content:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict) and block.get("type") == "text":
                            txt = block.get("text")
                            if txt:
                                parts.append(str(txt))
                    token = "".join(parts)
                else:
                    token = raw_content if isinstance(raw_content, str) else ""

                if token:
                    reasoning_key = call_run_id

                    def _emit(text: str, etype: str) -> dict:
                        if etype == "reasoning.delta":
                            if reasoning_key not in _reasoning_idx:
                                visit = node_visit_count.get(node, 0)
                                label = _NODE_MESSAGE.get(node, node)
                                if visit > 0:
                                    label += f" (attempt {visit + 1})"
                                _reasoning_idx[reasoning_key] = len(reasoning_entries)
                                reasoning_entries.append({
                                    "node": node,
                                    "run_id": call_run_id,
                                    "label": label,
                                    "tokens": [],
                                })
                                _step_reasoning_idx.setdefault(
                                    f"{node}:{node_visit_count.get(node, 0)}", []
                                ).append(_reasoning_idx[reasoning_key])
                            reasoning_entries[_reasoning_idx[reasoning_key]]["tokens"].append(text)
                        return {"event": etype, "data": {"node": node, "run_id": call_run_id, "text": text}}

                    if isinstance(s, MultiSectionStreamer):
                        text, etype = s.feed(token)
                        if text and etype:
                            ev_out = _emit(text, etype)
                            if node not in _NO_REASONING_NODES or etype != "reasoning.delta":
                                yield ev_out
                    else:
                        text = s.feed(token)
                        if text:
                            _, etype = _NODE_STREAM[node]
                            ev_out = _emit(text, etype)
                            if node not in _NO_REASONING_NODES or etype != "reasoning.delta":
                                yield ev_out

            elif kind == "on_custom_event":
                custom_data = ev.get("data", {})
                if custom_data.get("kind") == "subq_progress":
                    text = custom_data.get("text", "")
                    sq_id = custom_data.get("id", "")
                    if text:
                        call_key = f"{N.EXECUTOR}_subq:{sq_id}"
                        exec_visit = node_visit_count.get(N.EXECUTOR, 0)
                        reasoning_entries.append({
                            "node": N.EXECUTOR,
                            "run_id": call_key,
                            "label": f"Sub-question {sq_id}",
                            "tokens": [text],
                        })
                        _step_reasoning_idx.setdefault(
                            f"{N.EXECUTOR}:{exec_visit}", []
                        ).append(len(reasoning_entries) - 1)
                        yield {"event": "reasoning.delta", "data": {
                            "node": N.EXECUTOR,
                            "run_id": call_key,
                            "text": text,
                        }}

            elif kind == "on_chain_end":
                output = ev.get("data", {}).get("output")
                if isinstance(output, dict):
                    _visit_before_inc = node_visit_count.get(node, 0)
                    state.update({k: v for k, v in output.items() if k in _STATE_KEYS})

                    # Deterministic nodes: emit synthetic reasoning BEFORE closing
                    # the step so it gets attached to the step record in the DB.
                    _synthetic_text: str | None = None
                    if node == "ontology_lookup":
                        terms = state.get("ontology_terms", [])
                        if terms:
                            term_list = ", ".join(f"`lpp:{t['local']}`" for t in terms[:8])
                            if len(terms) > 8:
                                term_list += f" (+{len(terms) - 8} more)"
                            _synthetic_text = f"Resolved {len(terms)} ontology terms: {term_list}."
                        else:
                            _synthetic_text = "No ontology terms matched - using full ontology reference."

                    elif node == N.BRAIN_RETRIEVAL:
                        facts = state.get("tribal_facts", [])
                        routing = state.get("routing", "")
                        if routing == "kg_only":
                            _synthetic_text = "Policy context skipped - question answered from Knowledge Graph only."
                        elif facts:
                            fact_labels = ", ".join(
                                f"[{f.get('type', '?')}] {f.get('label', '?')}" for f in facts[:3]
                            )
                            if len(facts) > 3:
                                fact_labels += f" (+{len(facts) - 3} more)"
                            _synthetic_text = f"Retrieved {len(facts)} policy facts: {fact_labels}."
                        else:
                            _synthetic_text = "No relevant policy context found for this query."

                    elif node == N.SPARQL_VALIDATE:
                        err = state.get("sparql_error", "")
                        _synthetic_text = (
                            f"Validation failed — {err}"
                            if err
                            else "Syntax valid. All predicates confirmed."
                        )

                    elif node == N.VISUALIZATION:
                        viz = state.get("viz_spec")
                        if viz and isinstance(viz, dict) and viz.get("type"):
                            chart_type = viz.get("type", "")
                            x = viz.get("x_key", viz.get("name_key", ""))
                            y = viz.get("y_keys", [viz.get("y_key", viz.get("value_key", ""))])
                            y_str = ", ".join(y) if isinstance(y, list) else str(y)
                            _synthetic_text = f"Generated {chart_type} chart — {x} vs {y_str}."
                        else:
                            _synthetic_text = "No chart applicable for this result set."

                    elif node == N.STEP_REFLECTOR:
                        scratchpad = state.get("scratchpad", {})
                        passed = sum(1 for r in scratchpad.values() if r.get("status") == "completed")
                        skipped = sum(1 for r in scratchpad.values() if r.get("status") == "skipped")
                        needs_repair = sum(1 for r in scratchpad.values() if r.get("status") == "needs_repair")
                        parts = []
                        if passed:
                            parts.append(f"**{passed} passed**")
                        if skipped:
                            parts.append(f"*{skipped} skipped* (data not in graph)")
                        if needs_repair:
                            parts.append(f"`{needs_repair} flagged` for repair")
                        _synthetic_text = "Sub-result verdicts: " + ", ".join(parts) + "." if parts else "No sub-questions reflected."

                    if _synthetic_text is not None:
                        call_key = f"{node}:{_visit_before_inc}"
                        reasoning_entries.append({
                            "node": node,
                            "run_id": call_key,
                            "label": _NODE_MESSAGE.get(node, node),
                            "tokens": [_synthetic_text],
                        })
                        _step_reasoning_idx.setdefault(
                            f"{node}:{_visit_before_inc}", []
                        ).append(len(reasoning_entries) - 1)
                        yield {"event": "reasoning.delta", "data": {"node": node, "run_id": call_key, "text": _synthetic_text}}

                    node_visit_count[node] = _visit_before_inc + 1
                    node_dur = time.perf_counter() - node_timers.get(node, pipeline_start)
                    elapsed = time.perf_counter() - pipeline_start
                    logger.info(f"[{run_id}] {node} DONE | {node_dur:.1f}s | total +{elapsed:.1f}s")

                    # close the pipeline step with timing + accumulated reasoning
                    _step_visit_key = f"{node}:{_visit_before_inc}"
                    if _step_visit_key in _step_by_visit:
                        _s = _pipeline_steps[_step_by_visit[_step_visit_key]]
                        _s["status"] = "done"
                        _s["duration_ms"] = round(
                            (time.perf_counter() - _visit_timers.get(_step_visit_key, pipeline_start)) * 1000
                        )
                        _s["reasoning"] = "" if node in _NO_REASONING_NODES else "".join(
                            "".join(reasoning_entries[i]["tokens"])
                            for i in _step_reasoning_idx.get(_step_visit_key, [])
                        )

                    if node == N.SPARQL_EXECUTE:
                        status = "error" if state.get("sparql_error") else "success"
                        kg_rows = state.get("kg_rows", [])
                        yield {
                            "event": "execute.done",
                            "data": {
                                "status": status,
                                "sql": state.get("sparql", ""),
                                "columns": state.get("kg_columns", []),
                                "rows": kg_rows,
                                "row_count": state.get("kg_row_count", 0),
                                "will_visualize": bool(kg_rows),
                            },
                        }
                    elif node == N.SPARQL_GEN:
                        sparql = state.get("sparql", "")
                        if sparql:
                            yield {"event": "generate_sql", "data": {"sql": sparql}}

                    elif node == N.PLAN:
                        sub_qs = state.get("sub_questions", [])
                        if sub_qs:
                            yield {
                                "event": "plan.created",
                                "data": {
                                    "sub_questions": [{"id": s["id"], "question": s["question"]} for s in sub_qs],
                                    "count": len(sub_qs),
                                },
                            }

                    elif node == N.FINAL_REFLECTOR:
                        # Plan path: sparql_execute never runs in the outer graph, so emit
                        # execute.done here with aggregated data + all sub-question SPARQLs.
                        kg_rows = state.get("kg_rows", [])
                        scratchpad = state.get("scratchpad", {})
                        sparql_parts = [
                            f"-- {res.get('question', sqid)}\n{res['sparql']}"
                            for sqid, res in scratchpad.items()
                            if res.get("sparql")
                        ]
                        combined_sql = "\n\n".join(sparql_parts)
                        yield {
                            "event": "execute.done",
                            "data": {
                                "status": "success",
                                "sql": combined_sql,
                                "columns": state.get("kg_columns", []),
                                "rows": kg_rows,
                                "row_count": state.get("kg_row_count", 0),
                                "will_visualize": bool(kg_rows),
                            },
                        }

                    elif node == N.VISUALIZATION:
                        if state.get("viz_spec"):
                            yield {"event": "chart", "data": {"spec": state["viz_spec"]}}
                        else:
                            yield {"event": "viz.skip", "data": {}}

                    elif node == N.ANSWER_SYNTHESIS:
                        if state.get("follow_ups"):
                            yield {"event": "follow_ups", "data": {"questions": state["follow_ups"]}}

                    # Emit node.done so frontend can mark step complete immediately
                    # (without waiting for the final done event)
                    if _step_visit_key in _step_by_visit:
                        _done_step = _pipeline_steps[_step_by_visit[_step_visit_key]]
                        yield {
                            "event": "node.done",
                            "data": {
                                "node": node,
                                "duration_ms": _done_step["duration_ms"],
                            },
                        }

        total = time.perf_counter() - pipeline_start
        duration_ms = round(total * 1000)
        logger.info(f"[{run_id}] Pipeline DONE | {total:.1f}s | stopped={stopped}")
        _lf_trace_id = lf_handler.last_trace_id if lf_handler else None
        _lf_trace_url = _lf_make_public(_lf_trace_id) if _lf_trace_id else None
        try:
            yield {
                "event": "done",
                "data": {
                    "run_id": run_id,
                    "question": question,
                    "question_type": state.get("question_type", "kg_query"),
                    "stopped": stopped,
                    "persona": state.get("persona", ""),
                    "complexity": state.get("complexity", ""),
                    "intent": INTENT_DISPLAY_LABELS.get(state.get("intent", ""), state.get("intent", "")),
                    "sql": state.get("sparql", ""),
                    "columns": state.get("kg_columns", []),
                    "rows": state.get("kg_rows", []),
                    "row_count": state.get("kg_row_count", 0),
                    "chart_spec": state.get("viz_spec"),
                    "answer": state.get("answer", ""),
                    "follow_ups": state.get("follow_ups", []),
                    "governance_halt": state.get("governance_halt"),
                    "final_reflection": state.get("final_reflection", ""),
                    "sparql_error": state.get("sparql_error", ""),
                    "sparql_retries": state.get("sparql_retries", 0),
                    "duration_ms": duration_ms,
                    "langfuse_trace_id": _lf_trace_id,
                    "langfuse_trace_url": _lf_trace_url,
                    "token_usage": aggregate_token_usage(_token_usage_records),
                    "pipeline_steps": _pipeline_steps,
                    "reasoning": [
                        {
                            "node": e["node"],
                            "label": e["label"],
                            "text": "".join(e["tokens"]).strip(),
                        }
                        for e in reasoning_entries
                        if "".join(e["tokens"]).strip()
                    ],
                },
            }
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.warning(f"[{run_id}] Client disconnect before done: {e}")

        # ── Save episodic memory (cross-thread) ──────────────────────────────
        # Only save high-confidence memories: real data found, clean execution, not stopped.
        _should_save_memory = (
            not stopped
            and _memory_store is not None
            and user_id
            and state.get("question_type") == "kg_query"
            and (state.get("kg_row_count") or 0) > 0
            and not state.get("sparql_error")
            and bool(state.get("answer"))
        )
        if _should_save_memory:
            try:
                _mem_payload = {
                    "question": question,
                    "answer_summary": (state.get("answer") or "")[:500],
                    "sparql": (state.get("sparql") or "")[:1000],
                    "intent": state.get("intent") or "",
                    "ontology_terms": state.get("ontology_terms") or [],
                    "row_count": state.get("kg_row_count") or 0,
                }
                # Run in thread pool — embed_texts_sync makes blocking HTTP calls
                await asyncio.to_thread(
                    _memory_store.put,
                    (str(user_id), "mti_queries"),
                    str(thread_id),
                    _mem_payload,
                )
                logger.info(f"[{run_id}] Episodic memory saved: user={user_id} thread={thread_id} intent={state.get('intent', '')} rows={state.get('kg_row_count', 0)}")
            except Exception as e:
                logger.warning(f"[{run_id}] Memory save failed (non-fatal): {e}")

    except Exception as e:
        logger.error(f"[{run_id}] Pipeline error: {e}")
        yield {"event": "error", "data": {"message": "Something went wrong while processing your question. Please try again."}}
    finally:
        _active_streams.pop(thread_id, None)
        try:
            lf_ctx.__exit__(None, None, None)
        except Exception:
            pass
        _lf_flush()  # flush Langfuse events via v3 get_client().flush()

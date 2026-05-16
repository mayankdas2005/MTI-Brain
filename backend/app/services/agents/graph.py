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

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logger import logger
from app.services.agents import data_pool as dp
from app.services.agents.bedrock import init_llms
from app.services.agents.helpers import MultiSectionStreamer, SectionStreamer
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
SUMMARIZE_THRESHOLD = 20
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)

_checkpoint_pool: AsyncConnectionPool | None = None
_main_graph = None
_inner_graph = None

_active_streams: dict[str, asyncio.Event] = {}


# ─── Routing functions ────────────────────────────────────────────────────────

def route_after_intake(state: State) -> str:
    qt = state.get("question_type", "kg_query")
    if qt == "general_chat":
        return "general_chat"
    if qt == "rejected":
        return "rejected"
    complexity = state.get("complexity", "simple")
    if complexity == "advanced":
        return "plan"
    return "domain_specialist"


def route_after_validate(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return "sparql_gen"
        return "answer_synthesis"
    return "governance_gate"


def route_after_governance(state: State) -> str:
    if state.get("governance_halt"):
        return END
    return "sparql_execute"


def route_after_execute(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return "sparql_gen"
        return "answer_synthesis"
    return "graph_reasoning"


def route_after_verifier(state: State) -> str:
    if state.get("sparql_error"):
        if state.get("sparql_retries", 0) < MAX_SPARQL_RETRIES:
            return "sparql_gen"
    return "answer_synthesis"


def route_after_hil(state: State) -> str:
    return "compress"


def route_after_synthesis(state: State) -> str:
    if len(state.get("messages", [])) >= SUMMARIZE_THRESHOLD:
        return "compress"
    return END


def route_synthesis_to_viz(state: State) -> str:
    return "visualization" if state.get("kg_rows") else "skip_viz"


def route_after_general_chat(state: State) -> str:
    if len(state.get("messages", [])) >= SUMMARIZE_THRESHOLD:
        return "compress"
    return END


def route_after_plan_validator(state: State) -> str:
    halt = state.get("halt_reason")
    if halt:
        plan_attempts = state.get("plan_attempts", 0)
        if plan_attempts < 2:
            return "plan"
        return END
    return "executor"


def route_after_step_reflector(state: State) -> str:
    scratchpad = state.get("scratchpad", {})
    any_fail = any(
        r.get("status") == "needs_repair"
        for r in scratchpad.values()
    )
    if any_fail:
        return "repairer"
    return "final_reflector"


def route_after_repairer(state: State) -> str:
    halt = state.get("halt_reason")
    if halt:
        plan_attempts = state.get("plan_attempts", 0)
        if plan_attempts < 2:
            return "plan"
        return "final_reflector"
    sub_questions = state.get("sub_questions", [])
    has_pending = any(sq["status"] == "pending" for sq in sub_questions)
    if has_pending:
        return "executor"
    return "final_reflector"


# ─── Graph builders ───────────────────────────────────────────────────────────

def _build_inner_graph() -> StateGraph:
    """Inner pipeline: domain_specialist → visualization (12 nodes).

    Used by executor_node to run each sub-Q independently.
    No intake_classify; starts directly at domain_specialist.
    """
    b = StateGraph(State)

    b.add_node("domain_specialist", domain_specialist_node, retry_policy=LLM_RETRY)
    b.add_node("ontology_lookup", ontology_lookup_node)
    b.add_node("brain_retrieval", brain_retrieval_node)
    b.add_node("sparql_gen", sparql_gen_node, retry_policy=LLM_RETRY)
    b.add_node("sparql_validate", sparql_validate_node)
    b.add_node("governance_gate", governance_gate_node)
    b.add_node("sparql_execute", sparql_execute_node)
    b.add_node("graph_reasoning", graph_reasoning_node, retry_policy=LLM_RETRY)
    b.add_node("verifier", verifier_node)
    b.add_node("answer_synthesis", answer_synthesis_node, retry_policy=LLM_RETRY)
    b.add_node("visualization", visualization_node)

    b.add_edge(START, "domain_specialist")
    b.add_edge("domain_specialist", "ontology_lookup")
    b.add_edge("ontology_lookup", "brain_retrieval")
    b.add_edge("brain_retrieval", "sparql_gen")
    b.add_edge("sparql_gen", "sparql_validate")
    b.add_conditional_edges(
        "sparql_validate",
        route_after_validate,
        {"sparql_gen": "sparql_gen", "governance_gate": "governance_gate", "answer_synthesis": "answer_synthesis"},
    )
    b.add_conditional_edges(
        "governance_gate",
        route_after_governance,
        {"sparql_execute": "sparql_execute", END: END},
    )
    b.add_conditional_edges(
        "sparql_execute",
        route_after_execute,
        {"sparql_gen": "sparql_gen", "graph_reasoning": "graph_reasoning", "answer_synthesis": "answer_synthesis"},
    )
    b.add_edge("graph_reasoning", "verifier")
    b.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {"sparql_gen": "sparql_gen", "answer_synthesis": "answer_synthesis"},
    )
    b.add_conditional_edges(
        "answer_synthesis",
        route_synthesis_to_viz,
        {"visualization": "visualization", "skip_viz": END},
    )
    b.add_edge("visualization", END)

    return b


def _build_main_graph() -> StateGraph:
    """Main graph: intake_classify → routes to inner pipeline or outer loop."""
    b = StateGraph(State)

    # ── All nodes ──
    b.add_node("intake_classify", intake_classify_node, retry_policy=LLM_RETRY)
    b.add_node("general_chat", general_chat_node, retry_policy=LLM_RETRY)
    b.add_node("rejected", rejected_node)

    # Inner pipeline nodes
    b.add_node("domain_specialist", domain_specialist_node, retry_policy=LLM_RETRY)
    b.add_node("ontology_lookup", ontology_lookup_node)
    b.add_node("brain_retrieval", brain_retrieval_node)
    b.add_node("sparql_gen", sparql_gen_node, retry_policy=LLM_RETRY)
    b.add_node("sparql_validate", sparql_validate_node)
    b.add_node("governance_gate", governance_gate_node)
    b.add_node("sparql_execute", sparql_execute_node)
    b.add_node("graph_reasoning", graph_reasoning_node, retry_policy=LLM_RETRY)
    b.add_node("verifier", verifier_node)

    # Outer loop nodes
    b.add_node("plan", plan_node, retry_policy=LLM_RETRY)
    b.add_node("plan_validator", plan_validator_node)
    b.add_node("executor", executor_node)
    b.add_node("step_reflector", step_reflector_node, retry_policy=LLM_RETRY)
    b.add_node("repairer", repairer_node)
    b.add_node("final_reflector", final_reflector_node, retry_policy=LLM_RETRY)

    # Convergence nodes (shared by inner + outer paths)
    b.add_node("answer_synthesis", answer_synthesis_node, retry_policy=LLM_RETRY)
    b.add_node("visualization", visualization_node)
    b.add_node("human_in_loop", human_in_loop_node)
    b.add_node("compress", compress_node, retry_policy=LLM_RETRY)

    # ── Edges ──
    b.add_edge(START, "intake_classify")
    b.add_conditional_edges(
        "intake_classify",
        route_after_intake,
        {
            "general_chat": "general_chat",
            "rejected": "rejected",
            "domain_specialist": "domain_specialist",
            "plan": "plan",
        },
    )
    b.add_edge("rejected", END)
    b.add_conditional_edges(
        "general_chat",
        route_after_general_chat,
        {"compress": "compress", END: END},
    )

    # ── Inner pipeline path (simple / complex) ──
    b.add_edge("domain_specialist", "ontology_lookup")
    b.add_edge("ontology_lookup", "brain_retrieval")
    b.add_edge("brain_retrieval", "sparql_gen")
    b.add_edge("sparql_gen", "sparql_validate")
    b.add_conditional_edges(
        "sparql_validate",
        route_after_validate,
        {"sparql_gen": "sparql_gen", "governance_gate": "governance_gate", "answer_synthesis": "answer_synthesis"},
    )
    b.add_conditional_edges(
        "governance_gate",
        route_after_governance,
        {"sparql_execute": "sparql_execute", END: END},
    )
    b.add_conditional_edges(
        "sparql_execute",
        route_after_execute,
        {"sparql_gen": "sparql_gen", "graph_reasoning": "graph_reasoning", "answer_synthesis": "answer_synthesis"},
    )
    b.add_edge("graph_reasoning", "verifier")
    b.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {"sparql_gen": "sparql_gen", "answer_synthesis": "answer_synthesis"},
    )

    # ── Outer loop path (advanced) ──
    b.add_conditional_edges(
        "plan",
        lambda s: "plan_validator",
        {"plan_validator": "plan_validator"},
    )
    b.add_conditional_edges(
        "plan_validator",
        route_after_plan_validator,
        {"plan": "plan", "executor": "executor", END: END},
    )
    b.add_edge("executor", "step_reflector")
    b.add_conditional_edges(
        "step_reflector",
        route_after_step_reflector,
        {"repairer": "repairer", "final_reflector": "final_reflector"},
    )
    b.add_conditional_edges(
        "repairer",
        route_after_repairer,
        {"plan": "plan", "executor": "executor", "final_reflector": "final_reflector"},
    )
    b.add_edge("final_reflector", "answer_synthesis")

    # ── Convergence: both paths → answer_synthesis → visualization → HIL → compress → END ──
    b.add_conditional_edges(
        "answer_synthesis",
        route_synthesis_to_viz,
        {"visualization": "visualization", "skip_viz": "human_in_loop"},
    )
    b.add_edge("visualization", "human_in_loop")
    b.add_conditional_edges(
        "human_in_loop",
        route_after_hil,
        {"compress": "compress", END: END},
    )
    b.add_edge("compress", END)

    return b


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_pipeline() -> None:
    """Initialize LLMs, data pool, ontology, checkpoint store, and compile graphs."""
    global _checkpoint_pool, _main_graph, _inner_graph

    init_llms()
    init_ontology()
    await dp.init_data_pool()

    conninfo = settings.CHECKPOINT_CONNINFO
    async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
        try:
            await AsyncPostgresSaver(conn).setup()
        except UniqueViolation:
            pass

    _checkpoint_pool = AsyncConnectionPool(
        conninfo=conninfo,
        open=False,
        min_size=2,
        max_size=10,
        check=AsyncConnectionPool.check_connection,
        max_idle=300,
        kwargs={
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
    )
    await _checkpoint_pool.open()

    checkpointer = AsyncPostgresSaver(_checkpoint_pool)
    _main_graph = _build_main_graph().compile(checkpointer=checkpointer)
    _inner_graph = _build_inner_graph().compile()

    logger.info("MTI Brain pipeline initialized")


async def shutdown_pipeline() -> None:
    """Close data pool and checkpoint pool. Caller enforces the hard timeout."""
    global _checkpoint_pool
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
    "intake_classify": ("reasoning", "reasoning.delta"),
    "domain_specialist": ("reasoning", "reasoning.delta"),
    "sparql_gen": ("reasoning", "reasoning.delta"),
    "graph_reasoning": ("reasoning", "reasoning.delta"),
    "answer_synthesis": "multi",
    "plan": ("reasoning", "reasoning.delta"),
    "step_reflector": None,
    "final_reflector": ("reasoning", "reasoning.delta"),
    "general_chat": ("answer", "answer.delta"),
    "ontology_lookup": ("reasoning", "reasoning.delta"),
    "brain_retrieval": ("reasoning", "reasoning.delta"),
    "sparql_validate": ("reasoning", "reasoning.delta"),
    "sparql_execute": None,
    "governance_gate": None,
    "verifier": None,
    "visualization": ("reasoning", "reasoning.delta"),
    "plan_validator": None,
    "executor": ("reasoning", "reasoning.delta"),
    "repairer": None,
    "rejected": None,
}

_NODE_MESSAGE = {
    "intake_classify": "Understanding your question",
    "general_chat": "Responding",
    "rejected": "Request not supported",
    "domain_specialist": "Identifying data scope",
    "ontology_lookup": "Resolving ontology terms",
    "brain_retrieval": "Retrieving policy context",
    "sparql_gen": "Generating SPARQL query",
    "sparql_validate": "Validating query",
    "governance_gate": "Governance check",
    "sparql_execute": "Querying Knowledge Graph",
    "graph_reasoning": "Analyzing results",
    "answer_synthesis": "Preparing your answer",
    "visualization": "Building chart",
    "plan": "Planning sub-questions",
    "plan_validator": "Validating plan",
    "executor": "Executing sub-questions",
    "step_reflector": "Reflecting on sub-results",
    "repairer": "Repairing failed sub-queries",
    "final_reflector": "Final quality check",
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

    initial = {
        "question": question,
        "question_type": "",
        "persona": persona or "",
        "complexity": "",
        "intent": "",
        "routing": "",
        "ontology_terms": [],
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
        "summary": "",
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
        "domain_specialist", "ontology_lookup", "brain_retrieval",
        "sparql_gen", "sparql_validate", "governance_gate",
        "sparql_execute", "graph_reasoning", "verifier",
        "answer_synthesis", "visualization",
    }

    logger.info(
        f"[{run_id}] Pipeline START | thread={thread_id} | "
        f"deep_analysis={deep_analysis} | persona={persona!r} | max_rows={max_rows} | "
        f"question={question[:80]!r}"
    )

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
            if kind == "on_chain_start" and node == "executor":
                _executor_run_ids.add(run_id_str)

            # Filter events for inner sub-graph nodes while executor is active.
            # These are sub-question runs — their events must not surface at the outer level.
            if _executor_run_ids and node in _INNER_GRAPH_NODES:
                continue

            # Filter spurious executor on_chain_end events caused by inner graph
            # completions leaking as executor ends (different run_id than the real executor).
            if kind == "on_chain_end" and node == "executor":
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
                        call_key = f"executor_subq:{sq_id}"
                        exec_visit = node_visit_count.get("executor", 0)
                        reasoning_entries.append({
                            "node": "executor",
                            "run_id": call_key,
                            "label": f"Sub-question {sq_id}",
                            "tokens": [text],
                        })
                        _step_reasoning_idx.setdefault(
                            f"executor:{exec_visit}", []
                        ).append(len(reasoning_entries) - 1)
                        yield {"event": "reasoning.delta", "data": {
                            "node": "executor",
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
                            _synthetic_text = "No ontology terms matched — using full ontology reference."

                    elif node == "brain_retrieval":
                        facts = state.get("tribal_facts", [])
                        routing = state.get("routing", "")
                        if routing == "kg_only":
                            _synthetic_text = "Policy context skipped — question answered from Knowledge Graph only."
                        elif facts:
                            fact_labels = ", ".join(
                                f"[{f.get('type', '?')}] {f.get('label', '?')}" for f in facts[:3]
                            )
                            if len(facts) > 3:
                                fact_labels += f" (+{len(facts) - 3} more)"
                            _synthetic_text = f"Retrieved {len(facts)} policy facts: {fact_labels}."
                        else:
                            _synthetic_text = "No relevant policy context found for this query."

                    elif node == "sparql_validate":
                        err = state.get("sparql_error", "")
                        _synthetic_text = (
                            f"Validation failed — {err}"
                            if err
                            else "Syntax valid. All predicates confirmed."
                        )

                    elif node == "visualization":
                        viz = state.get("viz_spec")
                        if viz and isinstance(viz, dict) and viz.get("type"):
                            chart_type = viz.get("type", "")
                            x = viz.get("x_key", viz.get("name_key", ""))
                            y = viz.get("y_keys", [viz.get("y_key", viz.get("value_key", ""))])
                            y_str = ", ".join(y) if isinstance(y, list) else str(y)
                            _synthetic_text = f"Generated {chart_type} chart — {x} vs {y_str}."
                        else:
                            _synthetic_text = "No chart applicable for this result set."

                    elif node == "step_reflector":
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

                    if node == "sparql_execute":
                        status = "error" if state.get("sparql_error") else "success"
                        kg_rows = state.get("kg_rows", [])
                        yield {
                            "event": "sparql.executed",
                            "data": {
                                "status": status,
                                "sparql": state.get("sparql", ""),
                                "columns": state.get("kg_columns", []),
                                "rows": kg_rows,
                                "row_count": state.get("kg_row_count", 0),
                                "will_visualize": bool(kg_rows),
                            },
                        }
                    elif node == "sparql_gen":
                        sparql = state.get("sparql", "")
                        if sparql:
                            yield {"event": "sparql.generated", "data": {"query": sparql}}

                    elif node == "plan":
                        sub_qs = state.get("sub_questions", [])
                        if sub_qs:
                            yield {
                                "event": "plan.created",
                                "data": {
                                    "sub_questions": [{"id": s["id"], "question": s["question"]} for s in sub_qs],
                                    "count": len(sub_qs),
                                },
                            }

                    elif node == "visualization":
                        if state.get("viz_spec"):
                            yield {"event": "chart", "data": {"spec": state["viz_spec"]}}
                        else:
                            yield {"event": "viz.skip", "data": {}}

                    elif node == "answer_synthesis":
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
                    "intent": state.get("intent", ""),
                    "sparql": state.get("sparql", ""),
                    "columns": state.get("kg_columns", []),
                    "rows": state.get("kg_rows", []),
                    "row_count": state.get("kg_row_count", 0),
                    "chart_spec": state.get("viz_spec"),
                    "answer": state.get("answer", ""),
                    "follow_ups": state.get("follow_ups", []),
                    "governance_halt": state.get("governance_halt"),
                    "final_reflection": state.get("final_reflection", ""),
                    "duration_ms": duration_ms,
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

    except Exception as e:
        logger.error(f"[{run_id}] Pipeline error: {e}")
        yield {"event": "error", "data": {"message": "Something went wrong while processing your question. Please try again."}}
    finally:
        _active_streams.pop(thread_id, None)

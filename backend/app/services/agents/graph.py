"""LangGraph graph wiring for the Neo4j analytics pipeline.

13-node graph: intake → context_fetcher → intent_resolver → query_compiler
               → filter_resolver → sql_generator → sql_validator → executor → synthesis → chart_agent

Lifecycle (init/shutdown) and graph compilation live here.
Routing decisions live in routing.py.
Streaming lives in pipeline.py.

Entry points:
    from app.services.agents.graph import init_analytics_pipeline, shutdown_analytics_pipeline
    from app.services.agents.graph import compile_graph, get_compiled_graph
"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logger import logger
from app.services.agents import neo4j_client, redis_client
from app.services.agents.memory import long_term as lt_memory
from app.services.agents.node_names import (
    CHART_AGENT as N_CHART_AGENT,
    COMPRESS as N_COMPRESS,
    CONTEXT_FETCHER as N_CONTEXT_FETCHER,
    ERROR_RESPONSE as N_ERROR_RESPONSE,
    EXECUTOR as N_EXECUTOR,
    FILTER_RESOLVER as N_FILTER_RESOLVER,
    GENERAL_CHAT as N_GENERAL_CHAT,
    INTAKE as N_INTAKE,
    INTENT_RESOLVER as N_INTENT_RESOLVER,
    QUERY_COMPILER as N_QUERY_COMPILER,
    SQL_GENERATOR as N_SQL_GENERATOR,
    SQL_VALIDATOR as N_SQL_VALIDATOR,
    SYNTHESIS as N_SYNTHESIS,
)
from app.services.agents.nodes.chart_agent import chart_agent
from app.services.agents.nodes.compress import compress
from app.services.agents.nodes.context_fetcher import context_fetcher
from app.services.agents.nodes.error_response import error_response
from app.services.agents.nodes.executor import executor
from app.services.agents.nodes.filter_resolver import filter_resolver
from app.services.agents.nodes.general_chat import general_chat
from app.services.agents.nodes.intake_classifier import intake_classifier
from app.services.agents.nodes.intent_resolver import intent_resolver
from app.services.agents.nodes.query_compiler import query_compiler
from app.services.agents.nodes.sql_generator_node import sql_generator
from app.services.agents.nodes.sql_validator import sql_validator
from app.services.agents.nodes.synthesis import synthesis
from app.services.agents.routing import (
    LLM_RETRY,
    route_after_context_fetcher,
    route_compiler,
    route_executor,
    route_filter_resolver,
    route_intake,
    route_intent,
    route_should_compress,
    route_synthesis,
    route_validator,
)
from app.services.agents.state import AnalyticsState

_checkpoint_pool: AsyncConnectionPool | None = None
_compiled_graph = None
_memory_store = None
_memory_store_exit = None


def get_memory_store():
    return _memory_store


# ─── Graph builder ────────────────────────────────────────────────────────────

def compile_graph():
    """Build and compile the analytics LangGraph."""
    b = StateGraph(AnalyticsState)

    b.add_node(N_INTAKE,          intake_classifier, retry_policy=LLM_RETRY)
    b.add_node(N_GENERAL_CHAT,    general_chat,      retry_policy=LLM_RETRY)
    b.add_node(N_CONTEXT_FETCHER, context_fetcher)
    b.add_node(N_INTENT_RESOLVER, intent_resolver,   retry_policy=LLM_RETRY)
    b.add_node(N_QUERY_COMPILER,  query_compiler)
    b.add_node(N_FILTER_RESOLVER, filter_resolver,   retry_policy=LLM_RETRY)
    b.add_node(N_SQL_GENERATOR,   sql_generator,     retry_policy=LLM_RETRY)
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
        {N_QUERY_COMPILER: N_QUERY_COMPILER},
    )

    b.add_conditional_edges(
        N_QUERY_COMPILER,
        route_compiler,
        {N_FILTER_RESOLVER: N_FILTER_RESOLVER, N_SQL_GENERATOR: N_SQL_GENERATOR, N_ERROR_RESPONSE: N_ERROR_RESPONSE},
    )

    b.add_conditional_edges(
        N_FILTER_RESOLVER,
        route_filter_resolver,
        {N_SQL_GENERATOR: N_SQL_GENERATOR},
    )

    b.add_edge(N_SQL_GENERATOR, N_SQL_VALIDATOR)

    b.add_conditional_edges(
        N_SQL_VALIDATOR,
        route_validator,
        {N_SQL_GENERATOR: N_SQL_GENERATOR, N_EXECUTOR: N_EXECUTOR, N_ERROR_RESPONSE: N_ERROR_RESPONSE},
    )

    b.add_conditional_edges(
        N_EXECUTOR,
        route_executor,
        {
            N_SQL_VALIDATOR: N_SQL_VALIDATOR,
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
        from app.services.agents.bedrock import init_llms
        init_llms()
    except Exception as e:
        logger.warning("LLM init failed (non-fatal — will init on first use): {}", e)

    try:
        from app.services.agents.redshift_client import init_redshift
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
        lt_memory.set_memory_store(_memory_store, conninfo=conninfo_fast, embed_fn=embed_texts_sync)
        logger.info("Analytics memory store initialized")
    except Exception as e:
        logger.warning("Analytics memory store init failed (non-fatal): {}", e)
        _memory_store = None

    _compiled_graph = compile_graph().compile(checkpointer=checkpointer, store=_memory_store)

    # Fire background warmup tasks so the first real user query hits warm connections.
    # These run after compilation and do not block startup.
    import asyncio as _asyncio
    _asyncio.create_task(_warmup_pipeline())

    logger.info("Neo4j analytics pipeline initialized")


async def _warmup_pipeline() -> None:
    """Pre-warm Bedrock connections and the intake_classifier context cache.

    Runs as a fire-and-forget background task after init_analytics_pipeline().
    Any failure is non-fatal — the first real user query will pay the cold-start cost instead.
    """
    import asyncio

    async def _warmup_llms() -> None:
        from app.services.agents.bedrock import get_llm
        for tier in ("fast", "balanced"):
            try:
                llm = get_llm(tier)
                await llm.ainvoke([{"role": "user", "content": "hi"}])
                logger.info("warmup | Bedrock OK | tier={}", tier)
            except Exception as e:
                logger.warning("warmup | Bedrock failed (non-fatal) | tier={} | error={}", tier, e)

    async def _warmup_classifier() -> None:
        try:
            from app.services.agents.nodes.intake_classifier import warmup_classifier_context
            await warmup_classifier_context()
        except Exception as e:
            logger.warning("warmup | classifier context failed (non-fatal) | error={}", e)

    await asyncio.gather(_warmup_llms(), _warmup_classifier(), return_exceptions=True)
    logger.info("warmup | pipeline warmup complete")


async def shutdown_analytics_pipeline() -> None:
    """Shut down all analytics pipeline resources."""
    global _checkpoint_pool, _memory_store_exit

    try:
        from app.services.agents.redshift_client import close_redshift
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

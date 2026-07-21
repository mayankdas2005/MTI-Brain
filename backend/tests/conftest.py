"""Shared pytest fixtures for integration and e2e tests."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ─── Event loop ───


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─── App fixture (no real DB/Redis/LLM connections) ───


@pytest_asyncio.fixture
async def app():
    """FastAPI app with mocked lifespan (no real external connections)."""
    from app.main import app as _app

    # Override lifespan to skip real init (Bedrock, Redshift, Redis, Neo4j)
    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with DB session dependency overrides but NO auth override.

    Use this for testing 401 responses (unauthenticated requests).
    """
    from app.db.session import get_async_session, get_read_session

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.close = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_async_session] = _mock_session_gen
    app.dependency_overrides[get_read_session] = _mock_session_gen

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.mock_session = mock_session
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def authed_client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with auth AND DB overrides — for authenticated endpoint tests."""
    from app.db.session import get_async_session, get_read_session
    from app.api.v1.deps import get_current_user, CurrentUser

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.close = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    async def _mock_admin_user():
        return CurrentUser(
            id=TEST_USER_ID,
            email="test@example.com",
            name="Test User",
            groups=["user", "admin"],
        )

    app.dependency_overrides[get_async_session] = _mock_session_gen
    app.dependency_overrides[get_read_session] = _mock_session_gen
    app.dependency_overrides[get_current_user] = _mock_admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.mock_session = mock_session
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def user_client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with auth (user only, no admin) + DB overrides."""
    from app.db.session import get_async_session, get_read_session
    from app.api.v1.deps import get_current_user, CurrentUser

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.close = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    async def _mock_regular_user():
        return CurrentUser(
            id=TEST_USER_ID,
            email="test@example.com",
            name="Test User",
            groups=["user"],
        )

    app.dependency_overrides[get_async_session] = _mock_session_gen
    app.dependency_overrides[get_read_session] = _mock_session_gen
    app.dependency_overrides[get_current_user] = _mock_regular_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.mock_session = mock_session
        yield c

    app.dependency_overrides.clear()


# ─── Auth fixtures ───


@pytest.fixture
def auth_headers():
    """Generate a valid JWT token for authenticated requests."""
    from app.services.auth import create_jwt_token

    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
        token = create_jwt_token("test-user-id", "test@example.com", "Test User", ["user", "admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_headers():
    """JWT token with only 'user' group (no admin)."""
    from app.services.auth import create_jwt_token

    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
        token = create_jwt_token("test-user-id", "test@example.com", "Test User", ["user"])
    return {"Authorization": f"Bearer {token}"}


TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_THREAD_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TEST_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")


# ─── Database fixtures ───


@pytest_asyncio.fixture
async def db_session():
    """Mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    return session


# ─── LLM / Bedrock mock ───


@pytest.fixture
def mock_bedrock():
    """Mock AWS Bedrock LLM that returns configurable responses."""
    mock = MockBedrock()
    with patch("app.services.agents.bedrock._llm_map", mock._llm_map):
        yield mock


class MockBedrock:
    """Configurable mock for the Bedrock LLM layer."""

    def __init__(self):
        self._responses: dict[str, str] = {}
        self._llm_map = {
            "fast": self._make_llm("fast"),
            "balanced": self._make_llm("balanced"),
            "deep": self._make_llm("deep"),
        }

    def _make_llm(self, tier: str) -> MagicMock:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=self._get_response)
        return llm

    async def _get_response(self, *args, **kwargs):
        # Return based on the first matching key in the prompt
        prompt_text = str(args[0]) if args else ""
        for key, response in self._responses.items():
            if key.lower() in prompt_text.lower():
                mock_msg = MagicMock()
                mock_msg.content = response
                return mock_msg
        mock_msg = MagicMock()
        mock_msg.content = "{}"
        return mock_msg

    def set_response(self, key: str, response: str):
        """Set a response for prompts containing the given key."""
        self._responses[key] = response

    def set_responses(self, mapping: dict[str, str]):
        """Set multiple responses at once."""
        self._responses.update(mapping)

    def raise_circuit_breaker(self):
        """Make all LLM calls raise CircuitBreakerError."""
        from pybreaker import CircuitBreakerError
        for llm in self._llm_map.values():
            llm.ainvoke = AsyncMock(side_effect=CircuitBreakerError())


# ─── Redshift mock ───


@pytest.fixture
def mock_redshift():
    """Mock Redshift client for executor tests."""
    mock = MockRedshift()
    with patch("app.services.agents.redshift_client.execute_query", mock.execute_query):
        yield mock


class MockRedshift:
    """Configurable mock for Redshift query execution."""

    def __init__(self):
        self._results: list[dict] = []
        self._error: Exception | None = None

    def set_result(self, columns: list[str], rows: list[list]):
        """Set a successful query result."""
        self._results = [dict(zip(columns, row)) for row in rows]
        self._error = None

    def set_error(self, exc: Exception):
        """Make the next query raise an exception."""
        self._error = exc

    async def execute_query(self, sql: str, *args, **kwargs):
        if self._error:
            raise self._error
        return self._results


# ─── Neo4j mock ───


@pytest.fixture
def mock_neo4j():
    """Mock Neo4j client for context fetcher tests."""
    mock = MockNeo4j()
    with patch("app.services.agents.neo4j_client.run_query", mock.run_query):
        yield mock


class MockNeo4j:
    """Configurable mock for Neo4j graph queries."""

    def __init__(self):
        self._tables: list[str] = []
        self._nodes: list[dict] = []
        self._edges: list[dict] = []

    def set_tables(self, tables: list[str]):
        self._tables = tables
        self._nodes = [{"_label": "Table", "fqn": t} for t in tables]

    def set_graph(self, nodes: list[dict], edges: list[dict]):
        self._nodes = nodes
        self._edges = edges

    async def run_query(self, query: str, *args, **kwargs):
        if "table" in query.lower() or "Table" in query:
            return [{"fqn": t} for t in self._tables]
        return self._nodes


# ─── Redis mock ───


@pytest.fixture
def mock_redis():
    """fakeredis instance for cache/rate-limit tests."""
    try:
        import fakeredis.aioredis
        return fakeredis.aioredis.FakeRedis()
    except ImportError:
        return AsyncMock()


# ─── Sample pipeline states ───


@pytest.fixture
def sample_state():
    """Factory for minimal AnalyticsState dicts at various pipeline stages."""

    def _make(stage: str = "intake", **overrides) -> dict:
        base = {
            "thread_id": "test-thread-001",
            "user_id": "test-user",
            "messages": [{"role": "user", "content": "What was revenue last quarter?"}],
            "question": "What was revenue last quarter?",
        }

        stage_defaults = {
            "intake": {},
            "after_intake": {"question_type": "analytics"},
            "after_context": {
                "question_type": "analytics",
                "neo4j_raw_graph": {"nodes": [], "edges": []},
            },
            "after_anchor": {
                "question_type": "analytics",
                "anchor_tables_resolved": ["lpp.fact_media_metrics"],
            },
            "after_compiler": {
                "question_type": "analytics",
                "semantic_ir_list": [{"intent": "revenue", "anchor_tables": ["lpp.orders"]}],
            },
            "after_executor": {
                "question_type": "analytics",
                "result_list": [{"columns": ["date", "revenue"], "rows": [["2024-Q4", 1200000]]}],
            },
        }

        base.update(stage_defaults.get(stage, {}))
        base.update(overrides)
        return base

    return _make

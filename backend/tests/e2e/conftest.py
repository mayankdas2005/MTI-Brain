"""E2e-specific fixtures — mock only external I/O boundaries.

Unlike integration tests (which mock the service layer), these tests let the
actual FastAPI endpoints, service layer, and business logic execute for real.
Only external I/O is mocked:
  - Database session (no real test DB available)
  - AWS Bedrock LLM calls
  - Redshift query execution
  - Neo4j graph queries
  - Redis cache
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Re-use mock classes from root conftest
from tests.conftest import MockBedrock, MockNeo4j, MockRedshift, TEST_USER_ID


# ─── App with mocked lifespan ───


@pytest_asyncio.fixture
async def e2e_app():
    """FastAPI app with mocked lifespan (no real external connections)."""
    from app.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


# ─── Mock DB session that tracks calls ───


def _make_mock_db_session():
    """Create a mock DB session with realistic behaviour for common operations."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        fetchall=MagicMock(return_value=[]),
        fetchone=MagicMock(return_value=None),
        one_or_none=MagicMock(return_value=None),
        rowcount=0,
    ))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.begin_nested = MagicMock(return_value=_FakeNestedTransaction())
    return session


class _FakeNestedTransaction:
    """Async context manager that simulates a nested transaction (SAVEPOINT)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ─── Auth helpers ───


def _create_test_jwt(
    user_id: str = str(TEST_USER_ID),
    email: str = "e2e-user@example.com",
    name: str = "E2E Test User",
    groups: list[str] | None = None,
) -> str:
    """Create a JWT token that the app can validate."""
    from app.services.auth import create_jwt_token

    if groups is None:
        groups = ["user", "admin"]

    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
        token = create_jwt_token(user_id, email, name, groups)
    return token


@pytest.fixture
def e2e_auth_headers():
    """Authorization headers with a valid JWT for e2e tests."""
    token = _create_test_jwt()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def e2e_user_headers():
    """Authorization headers with user-only (no admin) role."""
    token = _create_test_jwt(groups=["user"])
    return {"Authorization": f"Bearer {token}"}


# ─── E2e client fixture ───


@pytest_asyncio.fixture
async def e2e_client(e2e_app, e2e_auth_headers) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with external boundaries mocked but services running for real.

    Mocks:
      - DB session (get_async_session, get_read_session)
      - JWT verification uses test signing key
      - Bedrock, Redshift, Neo4j are patched at module-level
    Does NOT mock:
      - FastAPI routing / middleware
      - Service layer (app.services.chat.conversation, app.services.auth, etc.)
      - Request validation / schema enforcement
    """
    from app.db.session import get_async_session, get_read_session

    mock_session = _make_mock_db_session()

    async def _mock_session_gen():
        yield mock_session

    e2e_app.dependency_overrides[get_async_session] = _mock_session_gen
    e2e_app.dependency_overrides[get_read_session] = _mock_session_gen

    # Patch JWT verification to use test signing key
    with patch("app.services.auth.settings") as mock_auth_settings:
        mock_auth_settings.JWT_ALGORITHM = "HS256"
        mock_auth_settings.jwt_verify_key = "test-secret-key-for-unit-tests-32chars!"
        mock_auth_settings.JWT_SECRET = "test-secret-key-for-unit-tests-32chars!"
        mock_auth_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
        mock_auth_settings.JWT_ACCESS_TOKEN_MINUTES = 60
        mock_auth_settings.JWT_REFRESH_TOKEN_DAYS = 7

        transport = ASGITransport(app=e2e_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            c.mock_session = mock_session
            c.auth_headers = e2e_auth_headers
            yield c

    e2e_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def e2e_unauthed_client(e2e_app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client without auth — for testing 401 and login flows."""
    from app.db.session import get_async_session, get_read_session

    mock_session = _make_mock_db_session()

    async def _mock_session_gen():
        yield mock_session

    e2e_app.dependency_overrides[get_async_session] = _mock_session_gen
    e2e_app.dependency_overrides[get_read_session] = _mock_session_gen

    transport = ASGITransport(app=e2e_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.mock_session = mock_session
        yield c

    e2e_app.dependency_overrides.clear()


# ─── External service mocks ───


@pytest.fixture
def mock_bedrock_e2e():
    """Mock Bedrock LLM at the I/O boundary for e2e tests."""
    mock = MockBedrock()
    with patch("app.services.agents.bedrock._llm_map", mock._llm_map):
        yield mock


@pytest.fixture
def mock_redshift_e2e():
    """Mock Redshift at the I/O boundary for e2e tests."""
    mock = MockRedshift()
    with patch("app.services.agents.redshift_client.execute_query", mock.execute_query):
        yield mock


@pytest.fixture
def mock_neo4j_e2e():
    """Mock Neo4j at the I/O boundary for e2e tests."""
    mock = MockNeo4j()
    with patch("app.services.agents.neo4j_client.run_query", mock.run_query):
        yield mock


@pytest.fixture
def mock_redis_e2e():
    """Mock Redis at the I/O boundary for e2e tests."""
    try:
        import fakeredis.aioredis
        return fakeredis.aioredis.FakeRedis()
    except ImportError:
        return AsyncMock()

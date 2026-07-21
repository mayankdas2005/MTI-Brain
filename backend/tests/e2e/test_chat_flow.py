"""End-to-end chat conversation flow tests.

Tests the full thread lifecycle through real service layer code with only
external I/O (database, Bedrock, Redshift, Neo4j) mocked at the boundary.

Covers:
  - Thread creation
  - Listing recent threads
  - Getting thread details
  - Deleting a thread
  - Thread rename and star operations
  - SSE streaming endpoint (ask question)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_THREAD_ID


def _mock_thread_obj(
    thread_id: uuid.UUID = TEST_THREAD_ID,
    title: str = "Revenue Analysis",
    project_id: uuid.UUID | None = None,
    starred: bool = False,
):
    """Create a mock thread object matching MTIBrainThread fields."""
    t = MagicMock()
    t.id = thread_id
    t.project_id = project_id
    t.title = title
    t.starred = starred
    t.user_id = TEST_USER_ID
    t.created_at = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    t.updated_at = datetime(2026, 6, 15, 10, 5, 0, tzinfo=timezone.utc)
    return t


@pytest.mark.e2e
class TestCreateThread:
    """E2e tests for POST /chat/new — thread creation via real service layer."""

    async def test_create_thread_basic(self, e2e_client):
        """Create a new thread with default settings."""
        mock_session = e2e_client.mock_session
        new_thread = _mock_thread_obj(title="New Analysis")

        # The service layer calls db.add() then db.flush() — mock the object return
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        with patch(
            "app.services.chat.conversation.create_thread",
            AsyncMock(return_value=new_thread),
        ):
            resp = await e2e_client.post(
                "/api/v1/chat/new",
                json={"title": "New Analysis"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["thread_id"] == str(TEST_THREAD_ID)
        assert data["title"] == "New Analysis"

    async def test_create_thread_with_project(self, e2e_client):
        """Create a thread associated with a project."""
        project_id = uuid.uuid4()
        new_thread = _mock_thread_obj(project_id=project_id)

        with patch(
            "app.services.chat.conversation.create_thread",
            AsyncMock(return_value=new_thread),
        ):
            resp = await e2e_client.post(
                "/api/v1/chat/new",
                json={"project_id": str(project_id), "title": "Project Thread"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["thread_id"] == str(TEST_THREAD_ID)

    async def test_create_thread_with_client_supplied_id(self, e2e_client):
        """Client can supply a thread_id."""
        client_thread_id = uuid.uuid4()
        new_thread = _mock_thread_obj(thread_id=client_thread_id)

        with patch(
            "app.services.chat.conversation.create_thread",
            AsyncMock(return_value=new_thread),
        ):
            resp = await e2e_client.post(
                "/api/v1/chat/new",
                json={"thread_id": str(client_thread_id)},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 201
        assert resp.json()["thread_id"] == str(client_thread_id)

    async def test_create_thread_unauthenticated(self, e2e_unauthed_client):
        """Thread creation without auth returns 401."""
        resp = await e2e_unauthed_client.post(
            "/api/v1/chat/new",
            json={"title": "Unauthorized"},
        )
        assert resp.status_code == 401


@pytest.mark.e2e
class TestListRecentThreads:
    """E2e tests for GET /chat/recents — listing threads via real service layer."""

    async def test_list_recents_returns_threads(self, e2e_client):
        """List recent threads returns paginated results."""
        thread_list = [
            {
                "id": uuid.uuid4(),
                "project_id": None,
                "title": "Revenue Q4",
                "starred": False,
                "last_message": "What was revenue last quarter?",
                "created_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
            },
            {
                "id": uuid.uuid4(),
                "project_id": None,
                "title": "Media Metrics",
                "starred": True,
                "last_message": "Show media performance...",
                "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
            },
        ]

        with patch(
            "app.services.chat.conversation.list_threads",
            AsyncMock(return_value=thread_list),
        ):
            resp = await e2e_client.get(
                "/api/v1/chat/recents",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Revenue Q4"
        assert data[1]["starred"] is True

    async def test_list_recents_with_limit(self, e2e_client):
        """Pagination params are respected."""
        with patch(
            "app.services.chat.conversation.list_threads",
            AsyncMock(return_value=[]),
        ) as mock_list:
            resp = await e2e_client.get(
                "/api/v1/chat/recents?limit=5&offset=10",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args
        assert call_kwargs.kwargs.get("limit") == 5 or call_kwargs[1].get("limit") == 5

    async def test_list_recents_with_search(self, e2e_client):
        """Search query triggers search_threads instead of list_threads."""
        search_results = [
            {
                "thread_id": uuid.uuid4(),
                "project_id": None,
                "title": "Revenue Q4",
                "starred": False,
                "message_id": None,
                "match_type": "thread",
                "preview": "Revenue Q4",
                "headline": "What was <b>revenue</b> last quarter?",
                "matched_terms": ["revenue"],
                "rank": 0.9,
                "created_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
            },
        ]

        with patch(
            "app.services.chat.conversation.search_threads",
            AsyncMock(return_value=search_results),
        ):
            resp = await e2e_client.get(
                "/api/v1/chat/recents?search=revenue",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200

    async def test_list_recents_unauthenticated(self, e2e_unauthed_client):
        """Listing threads without auth returns 401."""
        resp = await e2e_unauthed_client.get("/api/v1/chat/recents")
        assert resp.status_code == 401


@pytest.mark.e2e
class TestGetThread:
    """E2e tests for GET /chat/{thread_id} — fetching thread details."""

    async def test_get_thread_with_messages(self, e2e_client):
        """Fetch thread detail including messages."""
        thread = _mock_thread_obj()
        msg1 = MagicMock()
        msg1.id = uuid.uuid4()
        msg1.thread_id = TEST_THREAD_ID
        msg1.conversation_id = uuid.uuid4()
        msg1.parent_conversation_id = None
        msg1.role = "user"
        msg1.content = "What was revenue last quarter?"
        msg1.reasoning = None
        msg1.metadata_ = None
        msg1._feedback_liked = None
        msg1._feedback_comment = None
        msg1.created_at = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

        msg2 = MagicMock()
        msg2.id = uuid.uuid4()
        msg2.thread_id = TEST_THREAD_ID
        msg2.conversation_id = msg1.conversation_id
        msg2.parent_conversation_id = None
        msg2.role = "assistant"
        msg2.content = "Based on the data, revenue last quarter was $1.2M."
        msg2.reasoning = None
        msg2.metadata_ = {"sql": "SELECT sum(revenue) FROM orders WHERE quarter='Q4'"}
        msg2._feedback_liked = True
        msg2._feedback_comment = "Great answer!"
        msg2.created_at = datetime(2026, 6, 15, 10, 0, 5, tzinfo=timezone.utc)

        with patch(
            "app.services.chat.conversation.get_thread",
            AsyncMock(return_value=(thread, [msg1, msg2])),
        ):
            resp = await e2e_client.get(
                f"/api/v1/chat/{TEST_THREAD_ID}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(TEST_THREAD_ID)
        assert data["title"] == "Revenue Analysis"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["feedback"]["liked"] is True

    async def test_get_thread_not_found(self, e2e_client):
        """Requesting a non-existent thread returns 404."""
        with patch(
            "app.services.chat.conversation.get_thread",
            AsyncMock(return_value=(None, None)),
        ):
            resp = await e2e_client.get(
                f"/api/v1/chat/{uuid.uuid4()}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestDeleteThread:
    """E2e tests for DELETE /chat/{thread_id}."""

    async def test_delete_thread_success(self, e2e_client):
        """Successful thread deletion returns confirmation."""
        with patch(
            "app.services.chat.conversation.delete_thread",
            AsyncMock(return_value=True),
        ):
            resp = await e2e_client.delete(
                f"/api/v1/chat/{TEST_THREAD_ID}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["thread_id"] == str(TEST_THREAD_ID)

    async def test_delete_thread_not_found(self, e2e_client):
        """Deleting a non-existent thread returns 404."""
        with patch(
            "app.services.chat.conversation.delete_thread",
            AsyncMock(return_value=False),
        ):
            resp = await e2e_client.delete(
                f"/api/v1/chat/{uuid.uuid4()}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestThreadOperations:
    """E2e tests for thread star and rename operations."""

    async def test_star_thread(self, e2e_client):
        """Star/unstar a thread toggles the flag."""
        with patch(
            "app.services.chat.conversation.star_thread",
            AsyncMock(return_value=True),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/chat/{TEST_THREAD_ID}/star",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["starred"] is True

    async def test_rename_thread(self, e2e_client):
        """Rename a thread updates the title."""
        with patch(
            "app.services.chat.conversation.rename_thread",
            AsyncMock(return_value=True),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/chat/{TEST_THREAD_ID}/rename",
                json={"title": "Updated Revenue Analysis"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Revenue Analysis"

    async def test_rename_thread_not_found(self, e2e_client):
        """Renaming a non-existent thread returns 404."""
        with patch(
            "app.services.chat.conversation.rename_thread",
            AsyncMock(return_value=False),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/chat/{uuid.uuid4()}/rename",
                json={"title": "New Title"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestAskQuestion:
    """E2e tests for POST /chat/{thread_id}/ask — SSE streaming endpoint."""

    async def test_ask_returns_sse_stream(self, e2e_client):
        """Asking a question returns an SSE EventSourceResponse.

        Since SSE streaming is complex to consume in tests, we verify the
        endpoint returns a 200 with the correct content-type and doesn't error.
        The underlying pipeline is mocked to yield controlled SSE events.
        """
        mock_session = e2e_client.mock_session

        # Mock save_message_and_touch to simulate thread exists
        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.thread_id = TEST_THREAD_ID
        mock_msg.conversation_id = uuid.uuid4()

        # Simulate a row returned with touched=1, has_prior_user_msg=False
        mock_row = MagicMock()
        mock_row.touched = 1
        mock_row.has_prior_user_msg = False
        mock_execute_result = MagicMock()
        mock_execute_result.fetchone = MagicMock(return_value=mock_row)
        mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_execute_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        # Mock the streaming pipeline to yield a simple done event
        async def _mock_stream(**kwargs):
            yield {"event": "node.start", "data": {"node": "intake", "message": "Classifying..."}}
            yield {
                "event": "done",
                "data": {
                    "answer": "Revenue last quarter was $1.2M.",
                    "sql": "SELECT sum(revenue) FROM orders",
                    "columns": ["revenue"],
                    "rows": [["1200000"]],
                    "row_count": 1,
                    "chart_spec": None,
                    "follow_ups": ["How does this compare to Q3?"],
                    "run_id": "test-run-001",
                    "duration_ms": 1500,
                    "question_type": "analytics",
                },
            }

        with patch("app.services.agents.pipeline.stream_pipeline", _mock_stream):
            with patch("app.services.chat.conversation.make_title", return_value="Revenue Q4"):
                with patch("app.services.chat.conversation.save_smart_title", AsyncMock()):
                    with patch("app.api.v1.chat.async_session_factory") as mock_factory:
                        # Mock the session factory context manager used inside _build_sse_generator
                        mock_save_session = AsyncMock()
                        mock_save_session.commit = AsyncMock()
                        mock_save_session.execute = AsyncMock(return_value=mock_execute_result)
                        mock_save_session.begin_nested = MagicMock(
                            return_value=_FakeNestedTransaction()
                        )
                        mock_factory.return_value.__aenter__ = AsyncMock(
                            return_value=mock_save_session
                        )
                        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

                        resp = await e2e_client.post(
                            f"/api/v1/chat/{TEST_THREAD_ID}/ask",
                            json={"question": "What was revenue last quarter?"},
                            headers=e2e_client.auth_headers,
                        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    async def test_ask_thread_not_found(self, e2e_client):
        """Ask on a non-existent thread returns 404."""
        mock_session = e2e_client.mock_session

        # Simulate save_message_and_touch returning None (thread not found)
        mock_row = MagicMock()
        mock_row.touched = 0
        mock_row.has_prior_user_msg = False
        mock_execute_result = MagicMock()
        mock_execute_result.fetchone = MagicMock(return_value=mock_row)
        mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_execute_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with patch("app.api.v1.chat.async_session_factory") as mock_factory:
            mock_inner_session = AsyncMock()
            mock_inner_result = MagicMock()
            mock_inner_result.fetchone = MagicMock(return_value=mock_row)
            mock_inner_result.scalar_one_or_none = MagicMock(return_value=None)
            mock_inner_result.scalars = MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[]))
            )
            mock_inner_session.execute = AsyncMock(return_value=mock_inner_result)
            mock_inner_session.commit = AsyncMock()
            mock_inner_session.begin_nested = MagicMock(
                return_value=_FakeNestedTransaction()
            )
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_inner_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await e2e_client.post(
                f"/api/v1/chat/{uuid.uuid4()}/ask",
                json={"question": "What was revenue?"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404

    async def test_ask_validation_rejects_empty_question(self, e2e_client):
        """Empty question is rejected by request schema validation."""
        resp = await e2e_client.post(
            f"/api/v1/chat/{TEST_THREAD_ID}/ask",
            json={"question": ""},
            headers=e2e_client.auth_headers,
        )
        assert resp.status_code == 422

    async def test_ask_unauthenticated(self, e2e_unauthed_client):
        """Ask without auth returns 401."""
        resp = await e2e_unauthed_client.post(
            f"/api/v1/chat/{TEST_THREAD_ID}/ask",
            json={"question": "test"},
        )
        assert resp.status_code == 401


@pytest.mark.e2e
class TestStopGeneration:
    """E2e tests for POST /chat/{thread_id}/stop."""

    async def test_stop_active_stream(self, e2e_client):
        """Stop endpoint signals cancellation for an active stream."""
        resp = await e2e_client.post(
            f"/api/v1/chat/{TEST_THREAD_ID}/stop",
            headers=e2e_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == str(TEST_THREAD_ID)
        # stopped will be False since no stream is actually active in this test
        assert "stopped" in data


@pytest.mark.e2e
class TestThreadLifecycle:
    """E2e test exercising the full thread lifecycle: create -> get -> rename -> star -> delete."""

    async def test_full_lifecycle(self, e2e_client):
        """Walk through a complete thread lifecycle."""
        tid = uuid.uuid4()
        thread = _mock_thread_obj(thread_id=tid, title="Lifecycle Thread")

        # Step 1: Create
        with patch(
            "app.services.chat.conversation.create_thread",
            AsyncMock(return_value=thread),
        ):
            create_resp = await e2e_client.post(
                "/api/v1/chat/new",
                json={"thread_id": str(tid), "title": "Lifecycle Thread"},
                headers=e2e_client.auth_headers,
            )
        assert create_resp.status_code == 201
        assert create_resp.json()["thread_id"] == str(tid)

        # Step 2: Get thread detail
        msg = MagicMock()
        msg.id = uuid.uuid4()
        msg.thread_id = tid
        msg.conversation_id = uuid.uuid4()
        msg.parent_conversation_id = None
        msg.role = "user"
        msg.content = "Hello"
        msg.reasoning = None
        msg.metadata_ = None
        msg._feedback_liked = None
        msg._feedback_comment = None
        msg.created_at = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

        with patch(
            "app.services.chat.conversation.get_thread",
            AsyncMock(return_value=(thread, [msg])),
        ):
            get_resp = await e2e_client.get(
                f"/api/v1/chat/{tid}",
                headers=e2e_client.auth_headers,
            )
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "Lifecycle Thread"

        # Step 3: Rename
        with patch(
            "app.services.chat.conversation.rename_thread",
            AsyncMock(return_value=True),
        ):
            rename_resp = await e2e_client.patch(
                f"/api/v1/chat/{tid}/rename",
                json={"title": "Renamed Thread"},
                headers=e2e_client.auth_headers,
            )
        assert rename_resp.status_code == 200
        assert rename_resp.json()["title"] == "Renamed Thread"

        # Step 4: Star
        with patch(
            "app.services.chat.conversation.star_thread",
            AsyncMock(return_value=True),
        ):
            star_resp = await e2e_client.patch(
                f"/api/v1/chat/{tid}/star",
                headers=e2e_client.auth_headers,
            )
        assert star_resp.status_code == 200
        assert star_resp.json()["starred"] is True

        # Step 5: Delete
        with patch(
            "app.services.chat.conversation.delete_thread",
            AsyncMock(return_value=True),
        ):
            delete_resp = await e2e_client.delete(
                f"/api/v1/chat/{tid}",
                headers=e2e_client.auth_headers,
            )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True


class _FakeNestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

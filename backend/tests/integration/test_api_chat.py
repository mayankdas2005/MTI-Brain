"""Integration tests for chat API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_thread(id=None, title="Test Chat"):
    t = MagicMock()
    t.id = id or uuid.uuid4()
    t.title = title
    t.project_id = None
    t.starred = False
    t.created_at = datetime(2026, 1, 1)
    t.updated_at = datetime(2026, 1, 1)
    return t


def _thread_dict(id=None, title="Test Chat"):
    return {
        "id": id or uuid.uuid4(),
        "project_id": None,
        "title": title,
        "starred": False,
        "last_message": None,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
    }


@pytest.mark.integration
class TestNewChat:
    async def test_create_chat(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.create_thread = AsyncMock(return_value=_make_thread(id=tid, title="New Chat"))
            resp = await authed_client.post(
                "/api/v1/chat/new",
                json={"title": "New Chat"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["thread_id"] == str(tid)

    async def test_create_chat_with_project(self, authed_client):
        tid = uuid.uuid4()
        pid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.create_thread = AsyncMock(return_value=_make_thread(id=tid))
            resp = await authed_client.post(
                "/api/v1/chat/new",
                json={"project_id": str(pid)},
            )

        assert resp.status_code == 201

    async def test_create_chat_unauthorized(self, client):
        resp = await client.post("/api/v1/chat/new", json={})
        assert resp.status_code == 401


@pytest.mark.integration
class TestRecents:
    async def test_list_recents(self, authed_client):
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.list_threads = AsyncMock(return_value=[
                _thread_dict(title="Chat A"),
                _thread_dict(title="Chat B"),
            ])
            resp = await authed_client.get("/api/v1/chat/recents")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_recents_with_search(self, authed_client):
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.search_threads = AsyncMock(return_value=[{
                "thread_id": uuid.uuid4(),
                "title": "Revenue",
                "match_type": "title",
                "created_at": datetime(2026, 1, 1),
                "updated_at": datetime(2026, 1, 1),
            }])
            resp = await authed_client.get("/api/v1/chat/recents?search=revenue")

        assert resp.status_code == 200

    async def test_recents_with_limit(self, authed_client):
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.list_threads = AsyncMock(return_value=[])
            resp = await authed_client.get("/api/v1/chat/recents?limit=5")

        assert resp.status_code == 200


@pytest.mark.integration
class TestGetThread:
    async def test_get_thread(self, authed_client):
        tid = uuid.uuid4()
        thread = _make_thread(id=tid, title="My Thread")
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.get_thread = AsyncMock(return_value=(thread, []))
            resp = await authed_client.get(f"/api/v1/chat/{tid}")

        assert resp.status_code == 200

    async def test_get_thread_not_found(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.get_thread = AsyncMock(return_value=(None, None))
            resp = await authed_client.get(f"/api/v1/chat/{tid}")

        assert resp.status_code == 404


@pytest.mark.integration
class TestDeleteThread:
    async def test_delete_thread(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.delete_thread = AsyncMock(return_value=True)
            resp = await authed_client.delete(f"/api/v1/chat/{tid}")

        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    async def test_delete_thread_not_found(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.delete_thread = AsyncMock(return_value=False)
            resp = await authed_client.delete(f"/api/v1/chat/{tid}")

        assert resp.status_code == 404


@pytest.mark.integration
class TestStarThread:
    async def test_star_thread(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.star_thread = AsyncMock(return_value=True)
            resp = await authed_client.patch(f"/api/v1/chat/{tid}/star")

        assert resp.status_code == 200
        assert resp.json()["starred"] is True


@pytest.mark.integration
class TestRenameThread:
    async def test_rename_thread(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.rename_thread = AsyncMock(return_value=True)
            resp = await authed_client.patch(
                f"/api/v1/chat/{tid}/rename",
                json={"title": "New Title"},
            )

        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"


@pytest.mark.integration
class TestBulkOperations:
    async def test_bulk_delete(self, authed_client):
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.bulk_delete_threads = AsyncMock(return_value=2)
            resp = await authed_client.post(
                "/api/v1/chat/bulk/delete",
                json={"thread_ids": ids},
            )

        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 2

    async def test_bulk_move(self, authed_client):
        ids = [str(uuid.uuid4())]
        pid = str(uuid.uuid4())
        with patch("app.api.v1.chat.conv_service") as mock_svc:
            mock_svc.move_threads = AsyncMock(return_value=1)
            resp = await authed_client.post(
                "/api/v1/chat/bulk/move",
                json={"thread_ids": ids, "project_id": pid},
            )

        assert resp.status_code == 200
        assert resp.json()["moved_count"] == 1


@pytest.mark.integration
class TestFeedback:
    async def test_submit_feedback(self, authed_client):
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        fb_result = MagicMock()
        fb_result.id = uuid.uuid4()
        fb_result.conversation_id = cid
        fb_result.liked = True
        fb_result.comment = "Great answer"
        fb_result.created_at = datetime(2026, 1, 1)

        with patch("app.api.v1.chat.fb_service") as mock_fb:
            mock_fb.save_feedback = AsyncMock(return_value=(fb_result, None, None, None))
            mock_fb._build_intent_text = MagicMock(return_value=None)
            resp = await authed_client.post(
                f"/api/v1/chat/{tid}/conversations/{cid}/feedback",
                json={"liked": True, "comment": "Great answer"},
            )

        assert resp.status_code == 200

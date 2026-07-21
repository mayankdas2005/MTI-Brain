"""Integration tests for settings API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_instruction(id=None, title="Always be concise"):
    instr = MagicMock()
    instr.id = id or uuid.uuid4()
    instr.title = title
    instr.content = "Keep answers under 100 words"
    instr.enabled = True
    instr.scope = "all"
    instr.created_at = datetime(2026, 1, 1)
    instr.updated_at = datetime(2026, 1, 1)
    return instr


@pytest.mark.integration
class TestListInstructions:
    async def test_list_instructions(self, authed_client):
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(
            all=MagicMock(return_value=[_make_instruction(), _make_instruction(title="Use tables")])
        ))
        authed_client.mock_session.execute.return_value = mock_result

        resp = await authed_client.get("/api/v1/settings/instructions")
        assert resp.status_code == 200

    async def test_list_instructions_unauthorized(self, client):
        resp = await client.get("/api/v1/settings/instructions")
        assert resp.status_code == 401


@pytest.mark.integration
class TestCreateInstruction:
    async def test_create_instruction(self, authed_client):
        iid = uuid.uuid4()

        async def _mock_refresh(obj):
            obj.id = iid
            obj.created_at = datetime(2026, 1, 1)
            obj.updated_at = datetime(2026, 1, 1)

        authed_client.mock_session.refresh = AsyncMock(side_effect=_mock_refresh)

        resp = await authed_client.post(
            "/api/v1/settings/instructions",
            json={"title": "New Rule", "content": "Do this thing", "enabled": True, "scope": "all"},
        )
        assert resp.status_code == 201

    async def test_create_instruction_missing_content(self, authed_client):
        resp = await authed_client.post(
            "/api/v1/settings/instructions",
            json={"title": "No Content"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestDeleteInstruction:
    async def test_delete_instruction(self, authed_client):
        iid = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=_make_instruction(id=iid))
        authed_client.mock_session.execute.return_value = mock_result

        resp = await authed_client.delete(f"/api/v1/settings/instructions/{iid}")
        assert resp.status_code == 204

    async def test_delete_instruction_not_found(self, authed_client):
        iid = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        authed_client.mock_session.execute.return_value = mock_result

        resp = await authed_client.delete(f"/api/v1/settings/instructions/{iid}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestFeedbackHistory:
    async def test_get_feedback_page(self, authed_client):
        mock_count = MagicMock()
        mock_count.scalar_one = MagicMock(return_value=5)
        mock_rows = MagicMock()
        mock_rows.all = MagicMock(return_value=[])

        authed_client.mock_session.execute = AsyncMock(side_effect=[mock_count, mock_rows])

        resp = await authed_client.get("/api/v1/settings/feedback?page=1&per_page=10")
        assert resp.status_code == 200


@pytest.mark.integration
class TestAdminEndpoints:
    async def test_admin_query_patterns_requires_admin(self, user_client):
        resp = await user_client.get("/api/v1/settings/admin/query-patterns")
        assert resp.status_code == 403

    async def test_admin_query_patterns_with_admin(self, authed_client):
        with patch("app.services.agents.neo4j_client._neo4j_run") as mock_neo:
            mock_neo.side_effect = [
                [{"total": 0}],
                [{"n": 0}],
                [{"n": 0}],
                [],
            ]
            resp = await authed_client.get("/api/v1/settings/admin/query-patterns")

        assert resp.status_code == 200

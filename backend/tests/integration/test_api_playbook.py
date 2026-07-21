"""Integration tests for playbook (saved queries) API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_query(id=None, name="Revenue Query"):
    q = MagicMock()
    q.id = id or uuid.uuid4()
    q.name = name
    q.query_text = "What was total revenue last quarter?"
    q.user_id = uuid.uuid4()
    q.created_at = datetime(2026, 1, 1)
    q.updated_at = datetime(2026, 1, 1)
    return q


@pytest.mark.integration
class TestListPlaybook:
    async def test_list_saved_queries(self, authed_client):
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.list_saved_queries = AsyncMock(return_value=[
                _make_query(name="Revenue"),
                _make_query(name="Users"),
            ])
            resp = await authed_client.get("/api/v1/playbook")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_list_unauthorized(self, client):
        resp = await client.get("/api/v1/playbook")
        assert resp.status_code == 401


@pytest.mark.integration
class TestCreatePlaybook:
    async def test_create_saved_query(self, authed_client):
        new_query = _make_query(name="New Query")
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.create_saved_query = AsyncMock(return_value=new_query)
            resp = await authed_client.post(
                "/api/v1/playbook",
                json={"name": "New Query", "query_text": "What is AUM?"},
            )

        assert resp.status_code == 201

    async def test_create_missing_fields(self, authed_client):
        resp = await authed_client.post(
            "/api/v1/playbook",
            json={"name": "Incomplete"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestUpdatePlaybook:
    async def test_update_saved_query(self, authed_client):
        qid = uuid.uuid4()
        updated = _make_query(id=qid, name="Updated")
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.update_saved_query = AsyncMock(return_value=updated)
            resp = await authed_client.patch(
                f"/api/v1/playbook/{qid}",
                json={"name": "Updated"},
            )

        assert resp.status_code == 200

    async def test_update_not_found(self, authed_client):
        qid = uuid.uuid4()
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.update_saved_query = AsyncMock(return_value=None)
            resp = await authed_client.patch(
                f"/api/v1/playbook/{qid}",
                json={"name": "X"},
            )

        assert resp.status_code == 404


@pytest.mark.integration
class TestDeletePlaybook:
    async def test_delete_saved_query(self, authed_client):
        qid = uuid.uuid4()
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.delete_saved_query = AsyncMock(return_value=True)
            resp = await authed_client.delete(f"/api/v1/playbook/{qid}")

        assert resp.status_code == 204

    async def test_delete_not_found(self, authed_client):
        qid = uuid.uuid4()
        with patch("app.api.v1.playbook.svc") as mock_svc:
            mock_svc.delete_saved_query = AsyncMock(return_value=False)
            resp = await authed_client.delete(f"/api/v1/playbook/{qid}")

        assert resp.status_code == 404

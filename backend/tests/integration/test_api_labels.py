"""Integration tests for labels API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_label(id=None, label="Important", color="#ff0000"):
    lbl = MagicMock()
    lbl.id = id or uuid.uuid4()
    lbl.label = label
    lbl.color = color
    lbl.thread_id = uuid.uuid4()
    lbl.user_id = uuid.uuid4()
    lbl.created_at = datetime(2026, 1, 1)
    lbl.updated_at = datetime(2026, 1, 1)
    return lbl


@pytest.mark.integration
class TestListLabels:
    async def test_list_all_user_labels(self, authed_client):
        with patch("app.api.v1.labels.svc") as mock_svc:
            mock_svc.list_all_user_labels = AsyncMock(return_value=[
                _make_label(label="Bug"),
                _make_label(label="Feature"),
            ])
            resp = await authed_client.get("/api/v1/labels")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_list_labels_unauthorized(self, client):
        resp = await client.get("/api/v1/labels")
        assert resp.status_code == 401


@pytest.mark.integration
class TestThreadLabels:
    async def test_get_thread_labels(self, authed_client):
        tid = uuid.uuid4()
        with patch("app.api.v1.labels.svc") as mock_svc:
            mock_svc.list_thread_labels = AsyncMock(return_value=[_make_label()])
            resp = await authed_client.get(f"/api/v1/labels/thread/{tid}")

        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_add_thread_label(self, authed_client):
        tid = uuid.uuid4()
        new_label = _make_label(label="Priority", color="#00ff00")
        with patch("app.api.v1.labels.svc") as mock_svc:
            mock_svc.add_thread_label = AsyncMock(return_value=new_label)
            resp = await authed_client.post(
                f"/api/v1/labels/thread/{tid}",
                json={"label": "Priority", "color": "#00ff00"},
            )

        assert resp.status_code == 201


@pytest.mark.integration
class TestDeleteLabel:
    async def test_delete_label(self, authed_client):
        lid = uuid.uuid4()
        with patch("app.api.v1.labels.svc") as mock_svc:
            mock_svc.delete_thread_label = AsyncMock(return_value=True)
            resp = await authed_client.delete(f"/api/v1/labels/{lid}")

        assert resp.status_code == 204

    async def test_delete_label_not_found(self, authed_client):
        lid = uuid.uuid4()
        with patch("app.api.v1.labels.svc") as mock_svc:
            mock_svc.delete_thread_label = AsyncMock(return_value=False)
            resp = await authed_client.delete(f"/api/v1/labels/{lid}")

        assert resp.status_code == 404

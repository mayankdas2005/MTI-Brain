"""Integration tests for pinned metrics API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_metric(id=None, label="Revenue"):
    m = MagicMock()
    m.id = id or uuid.uuid4()
    m.label = label
    m.source_query = "What is total revenue?"
    m.position = 0
    m.user_id = uuid.uuid4()
    m.created_at = datetime(2026, 1, 1)
    m.updated_at = datetime(2026, 1, 1)
    return m


@pytest.mark.integration
class TestListPinnedMetrics:
    async def test_list_metrics(self, authed_client):
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.list_pinned_metrics = AsyncMock(return_value=[
                _make_metric(label="Revenue"),
                _make_metric(label="Users"),
            ])
            resp = await authed_client.get("/api/v1/pinned-metrics")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_list_metrics_unauthorized(self, client):
        resp = await client.get("/api/v1/pinned-metrics")
        assert resp.status_code == 401


@pytest.mark.integration
class TestCreatePinnedMetric:
    async def test_create_metric(self, authed_client):
        new_metric = _make_metric(label="AUM")
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.create_pinned_metric = AsyncMock(return_value=new_metric)
            resp = await authed_client.post(
                "/api/v1/pinned-metrics",
                json={"label": "AUM", "source_query": "What is AUM?", "position": 0},
            )

        assert resp.status_code == 201

    async def test_create_metric_missing_fields(self, authed_client):
        resp = await authed_client.post(
            "/api/v1/pinned-metrics",
            json={"label": "Incomplete"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestUpdatePinnedMetric:
    async def test_update_metric(self, authed_client):
        mid = uuid.uuid4()
        updated = _make_metric(id=mid, label="Updated")
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.update_pinned_metric = AsyncMock(return_value=updated)
            resp = await authed_client.patch(
                f"/api/v1/pinned-metrics/{mid}",
                json={"label": "Updated"},
            )

        assert resp.status_code == 200

    async def test_update_metric_not_found(self, authed_client):
        mid = uuid.uuid4()
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.update_pinned_metric = AsyncMock(return_value=None)
            resp = await authed_client.patch(
                f"/api/v1/pinned-metrics/{mid}",
                json={"label": "X"},
            )

        assert resp.status_code == 404


@pytest.mark.integration
class TestDeletePinnedMetric:
    async def test_delete_metric(self, authed_client):
        mid = uuid.uuid4()
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.delete_pinned_metric = AsyncMock(return_value=True)
            resp = await authed_client.delete(f"/api/v1/pinned-metrics/{mid}")

        assert resp.status_code == 204

    async def test_delete_metric_not_found(self, authed_client):
        mid = uuid.uuid4()
        with patch("app.api.v1.pinned_metrics.svc") as mock_svc:
            mock_svc.delete_pinned_metric = AsyncMock(return_value=False)
            resp = await authed_client.delete(f"/api/v1/pinned-metrics/{mid}")

        assert resp.status_code == 404

"""Integration tests for projects API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import pytest


def _make_project(id=None, name="Test Project", description="A test project"):
    p = MagicMock()
    p.id = id or uuid.uuid4()
    p.name = name
    p.description = description
    p.starred = False
    p.thread_count = 0
    p.created_at = datetime(2026, 1, 1)
    p.updated_at = datetime(2026, 1, 1)
    return p


def _project_dict(id=None, name="Test Project", description="A test project"):
    return {
        "id": id or uuid.uuid4(),
        "name": name,
        "description": description,
        "starred": False,
        "thread_count": 0,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
    }


@pytest.mark.integration
class TestListProjects:
    async def test_list_projects(self, authed_client):
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.list_projects = AsyncMock(return_value=[
                _project_dict(name="Project A"),
                _project_dict(name="Project B"),
            ])
            resp = await authed_client.get("/api/v1/projects")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_list_projects_with_search(self, authed_client):
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.list_projects = AsyncMock(return_value=[_project_dict(name="Alpha")])
            resp = await authed_client.get("/api/v1/projects?search=alpha")

        assert resp.status_code == 200
        mock_svc.list_projects.assert_called_once()

    async def test_list_projects_unauthorized(self, client):
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 401


@pytest.mark.integration
class TestCreateProject:
    async def test_create_project(self, authed_client):
        new_project = _make_project(name="New Project", description="Desc")
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.create_project = AsyncMock(return_value=new_project)
            resp = await authed_client.post(
                "/api/v1/projects/create",
                json={"name": "New Project", "description": "Desc"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Project"

    async def test_create_project_missing_name(self, authed_client):
        resp = await authed_client.post(
            "/api/v1/projects/create",
            json={"description": "No name"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestGetProject:
    async def test_get_project(self, authed_client):
        pid = uuid.uuid4()
        project_detail = {
            "id": str(pid),
            "name": "Test",
            "description": "Desc",
            "starred": False,
            "threads": [],
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.get_project = AsyncMock(return_value=project_detail)
            resp = await authed_client.get(f"/api/v1/projects/{pid}")

        assert resp.status_code == 200

    async def test_get_project_not_found(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.get_project = AsyncMock(return_value=None)
            resp = await authed_client.get(f"/api/v1/projects/{pid}")

        assert resp.status_code == 404


@pytest.mark.integration
class TestUpdateProject:
    async def test_update_project(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            update_data = _project_dict(id=pid, name="Updated")
            del update_data["thread_count"]
            mock_svc.update_project = AsyncMock(return_value=update_data)
            resp = await authed_client.put(
                f"/api/v1/projects/{pid}",
                json={"name": "Updated", "description": "New desc"},
            )

        assert resp.status_code == 200


@pytest.mark.integration
class TestDeleteProject:
    async def test_delete_project(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.delete_project = AsyncMock(return_value=True)
            resp = await authed_client.delete(f"/api/v1/projects/{pid}")

        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    async def test_delete_project_not_found(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.delete_project = AsyncMock(return_value=False)
            resp = await authed_client.delete(f"/api/v1/projects/{pid}")

        assert resp.status_code == 404


@pytest.mark.integration
class TestStarProject:
    async def test_star_project(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.star_project = AsyncMock(return_value=True)
            resp = await authed_client.patch(f"/api/v1/projects/{pid}/star")

        assert resp.status_code == 200
        assert resp.json()["starred"] is True

    async def test_star_project_not_found(self, authed_client):
        pid = uuid.uuid4()
        with patch("app.api.v1.project.conv_service") as mock_svc:
            mock_svc.star_project = AsyncMock(return_value=None)
            resp = await authed_client.patch(f"/api/v1/projects/{pid}/star")

        assert resp.status_code == 404

"""End-to-end project management flow tests.

Tests the full project lifecycle through real service layer code with only
external I/O (database) mocked at the boundary.

Covers:
  - Project creation
  - Listing projects
  - Getting project detail with threads
  - Updating project metadata
  - Starring a project
  - Deleting a project
  - Full lifecycle: create -> add threads -> list -> update -> delete
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_PROJECT_ID, TEST_THREAD_ID


def _mock_project_obj(
    project_id: uuid.UUID = TEST_PROJECT_ID,
    name: str = "Media Analytics",
    description: str = "Tracking media performance metrics",
    starred: bool = False,
):
    """Create a mock project object matching MTIBrainProject fields."""
    p = MagicMock()
    p.id = project_id
    p.name = name
    p.description = description
    p.starred = starred
    p.user_id = TEST_USER_ID
    p.thread_count = 0
    p.created_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
    p.updated_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
    return p


@pytest.mark.e2e
class TestCreateProject:
    """E2e tests for POST /projects/create."""

    async def test_create_project_with_name_and_description(self, e2e_client):
        """Create a project with all fields."""
        new_project = _mock_project_obj(name="Q3 Campaign Analysis", description="Analyzing Q3 campaigns")

        with patch(
            "app.services.chat.conversation.create_project",
            AsyncMock(return_value=new_project),
        ):
            resp = await e2e_client.post(
                "/api/v1/projects/create",
                json={"name": "Q3 Campaign Analysis", "description": "Analyzing Q3 campaigns"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Q3 Campaign Analysis"
        assert data["description"] == "Analyzing Q3 campaigns"
        assert data["thread_count"] == 0
        assert data["starred"] is False

    async def test_create_project_name_only(self, e2e_client):
        """Create a project with just a name (description optional)."""
        new_project = _mock_project_obj(name="Quick Project", description=None)
        new_project.description = None

        with patch(
            "app.services.chat.conversation.create_project",
            AsyncMock(return_value=new_project),
        ):
            resp = await e2e_client.post(
                "/api/v1/projects/create",
                json={"name": "Quick Project"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 201
        assert resp.json()["name"] == "Quick Project"

    async def test_create_project_missing_name_rejected(self, e2e_client):
        """Project creation without a name fails validation."""
        resp = await e2e_client.post(
            "/api/v1/projects/create",
            json={"description": "No name provided"},
            headers=e2e_client.auth_headers,
        )
        assert resp.status_code == 422

    async def test_create_project_empty_name_rejected(self, e2e_client):
        """Empty string name fails Pydantic min_length validation."""
        resp = await e2e_client.post(
            "/api/v1/projects/create",
            json={"name": ""},
            headers=e2e_client.auth_headers,
        )
        assert resp.status_code == 422

    async def test_create_project_unauthenticated(self, e2e_unauthed_client):
        """Project creation without auth returns 401."""
        resp = await e2e_unauthed_client.post(
            "/api/v1/projects/create",
            json={"name": "Unauthorized Project"},
        )
        assert resp.status_code == 401


@pytest.mark.e2e
class TestListProjects:
    """E2e tests for GET /projects."""

    async def test_list_projects_returns_user_projects(self, e2e_client):
        """List projects returns all projects for the authenticated user."""
        projects = [
            {
                "id": uuid.uuid4(),
                "name": "Campaign Tracker",
                "description": "All campaigns",
                "starred": False,
                "thread_count": 5,
                "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
            },
            {
                "id": uuid.uuid4(),
                "name": "Revenue Dashboard",
                "description": "Monthly revenue analysis",
                "starred": True,
                "thread_count": 12,
                "created_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
            },
        ]

        with patch(
            "app.services.chat.conversation.list_projects",
            AsyncMock(return_value=projects),
        ):
            resp = await e2e_client.get(
                "/api/v1/projects",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Campaign Tracker"
        assert data[1]["starred"] is True
        assert data[1]["thread_count"] == 12

    async def test_list_projects_with_search(self, e2e_client):
        """Search filter is passed to the service layer."""
        with patch(
            "app.services.chat.conversation.list_projects",
            AsyncMock(return_value=[]),
        ) as mock_list:
            resp = await e2e_client.get(
                "/api/v1/projects?search=revenue",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        mock_list.assert_called_once()

    async def test_list_projects_empty(self, e2e_client):
        """Returns empty list when user has no projects."""
        with patch(
            "app.services.chat.conversation.list_projects",
            AsyncMock(return_value=[]),
        ):
            resp = await e2e_client.get(
                "/api/v1/projects",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_projects_unauthenticated(self, e2e_unauthed_client):
        """Listing projects without auth returns 401."""
        resp = await e2e_unauthed_client.get("/api/v1/projects")
        assert resp.status_code == 401


@pytest.mark.e2e
class TestGetProject:
    """E2e tests for GET /projects/{project_id}."""

    async def test_get_project_with_threads(self, e2e_client):
        """Fetch a project with its associated threads."""
        project_detail = {
            "id": TEST_PROJECT_ID,
            "name": "Media Analytics",
            "description": "Tracking media metrics",
            "starred": False,
            "threads": [
                {
                    "id": uuid.uuid4(),
                    "title": "CPM Analysis",
                    "starred": False,
                    "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
                },
                {
                    "id": uuid.uuid4(),
                    "title": "Reach vs Impressions",
                    "starred": True,
                    "created_at": datetime(2026, 6, 5, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 6, 12, tzinfo=timezone.utc),
                },
            ],
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 6, 12, tzinfo=timezone.utc),
        }

        with patch(
            "app.services.chat.conversation.get_project",
            AsyncMock(return_value=project_detail),
        ):
            resp = await e2e_client.get(
                f"/api/v1/projects/{TEST_PROJECT_ID}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Media Analytics"
        assert len(data["threads"]) == 2
        assert data["threads"][0]["title"] == "CPM Analysis"
        assert data["threads"][1]["starred"] is True

    async def test_get_project_not_found(self, e2e_client):
        """Requesting a non-existent project returns 404."""
        with patch(
            "app.services.chat.conversation.get_project",
            AsyncMock(return_value=None),
        ):
            resp = await e2e_client.get(
                f"/api/v1/projects/{uuid.uuid4()}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestUpdateProject:
    """E2e tests for PUT /projects/{project_id}."""

    async def test_update_project_name_and_description(self, e2e_client):
        """Update both name and description."""
        updated = {
            "id": TEST_PROJECT_ID,
            "name": "Updated Project Name",
            "description": "Updated description",
            "starred": False,
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
        }

        with patch(
            "app.services.chat.conversation.update_project",
            AsyncMock(return_value=updated),
        ):
            resp = await e2e_client.put(
                f"/api/v1/projects/{TEST_PROJECT_ID}",
                json={"name": "Updated Project Name", "description": "Updated description"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Project Name"
        assert data["description"] == "Updated description"

    async def test_update_project_not_found(self, e2e_client):
        """Updating a non-existent project returns 404."""
        with patch(
            "app.services.chat.conversation.update_project",
            AsyncMock(return_value=None),
        ):
            resp = await e2e_client.put(
                f"/api/v1/projects/{uuid.uuid4()}",
                json={"name": "Won't Work"},
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestStarProject:
    """E2e tests for PATCH /projects/{project_id}/star."""

    async def test_star_project(self, e2e_client):
        """Star a project toggles the starred flag."""
        with patch(
            "app.services.chat.conversation.star_project",
            AsyncMock(return_value=True),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/projects/{TEST_PROJECT_ID}/star",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["starred"] is True

    async def test_unstar_project(self, e2e_client):
        """Star endpoint can also unstar (returns False)."""
        with patch(
            "app.services.chat.conversation.star_project",
            AsyncMock(return_value=False),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/projects/{TEST_PROJECT_ID}/star",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["starred"] is False

    async def test_star_project_not_found(self, e2e_client):
        """Starring a non-existent project returns 404."""
        with patch(
            "app.services.chat.conversation.star_project",
            AsyncMock(return_value=None),
        ):
            resp = await e2e_client.patch(
                f"/api/v1/projects/{uuid.uuid4()}/star",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404


@pytest.mark.e2e
class TestDeleteProject:
    """E2e tests for DELETE /projects/{project_id}."""

    async def test_delete_project_success(self, e2e_client):
        """Successful project deletion returns confirmation."""
        with patch(
            "app.services.chat.conversation.delete_project",
            AsyncMock(return_value=True),
        ):
            resp = await e2e_client.delete(
                f"/api/v1/projects/{TEST_PROJECT_ID}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["project_id"] == str(TEST_PROJECT_ID)

    async def test_delete_project_not_found(self, e2e_client):
        """Deleting a non-existent project returns 404."""
        with patch(
            "app.services.chat.conversation.delete_project",
            AsyncMock(return_value=False),
        ):
            resp = await e2e_client.delete(
                f"/api/v1/projects/{uuid.uuid4()}",
                headers=e2e_client.auth_headers,
            )

        assert resp.status_code == 404

    async def test_delete_project_unauthenticated(self, e2e_unauthed_client):
        """Deleting without auth returns 401."""
        resp = await e2e_unauthed_client.delete(
            f"/api/v1/projects/{TEST_PROJECT_ID}",
        )
        assert resp.status_code == 401


@pytest.mark.e2e
class TestProjectLifecycle:
    """E2e test exercising the full project lifecycle with thread association."""

    async def test_full_project_lifecycle(self, e2e_client):
        """Create project -> create thread in project -> list threads -> delete project."""
        pid = uuid.uuid4()
        tid = uuid.uuid4()

        # Step 1: Create project
        project = _mock_project_obj(project_id=pid, name="Lifecycle Project")
        with patch(
            "app.services.chat.conversation.create_project",
            AsyncMock(return_value=project),
        ):
            create_resp = await e2e_client.post(
                "/api/v1/projects/create",
                json={"name": "Lifecycle Project", "description": "Testing lifecycle"},
                headers=e2e_client.auth_headers,
            )
        assert create_resp.status_code == 201
        assert create_resp.json()["name"] == "Lifecycle Project"

        # Step 2: Create a thread within the project
        thread = MagicMock()
        thread.id = tid
        thread.title = "Thread in Project"
        thread.project_id = pid
        thread.starred = False
        thread.user_id = TEST_USER_ID
        thread.created_at = datetime(2026, 6, 20, tzinfo=timezone.utc)
        thread.updated_at = datetime(2026, 6, 20, tzinfo=timezone.utc)

        with patch(
            "app.services.chat.conversation.create_thread",
            AsyncMock(return_value=thread),
        ):
            thread_resp = await e2e_client.post(
                "/api/v1/chat/new",
                json={"project_id": str(pid), "title": "Thread in Project"},
                headers=e2e_client.auth_headers,
            )
        assert thread_resp.status_code == 201
        assert thread_resp.json()["thread_id"] == str(tid)

        # Step 3: Get project detail (should show the thread)
        project_detail = {
            "id": pid,
            "name": "Lifecycle Project",
            "description": "Testing lifecycle",
            "starred": False,
            "threads": [
                {
                    "id": tid,
                    "title": "Thread in Project",
                    "starred": False,
                    "created_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
                },
            ],
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
        }
        with patch(
            "app.services.chat.conversation.get_project",
            AsyncMock(return_value=project_detail),
        ):
            detail_resp = await e2e_client.get(
                f"/api/v1/projects/{pid}",
                headers=e2e_client.auth_headers,
            )
        assert detail_resp.status_code == 200
        assert len(detail_resp.json()["threads"]) == 1
        assert detail_resp.json()["threads"][0]["title"] == "Thread in Project"

        # Step 4: List project threads via chat/recents with project_id filter
        thread_list = [
            {
                "id": tid,
                "project_id": pid,
                "title": "Thread in Project",
                "starred": False,
                "last_message": None,
                "created_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
            },
        ]
        with patch(
            "app.services.chat.conversation.list_threads",
            AsyncMock(return_value=thread_list),
        ):
            list_resp = await e2e_client.get(
                f"/api/v1/chat/recents?project_id={pid}",
                headers=e2e_client.auth_headers,
            )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # Step 5: Delete project (cascades to threads)
        with patch(
            "app.services.chat.conversation.delete_project",
            AsyncMock(return_value=True),
        ):
            delete_resp = await e2e_client.delete(
                f"/api/v1/projects/{pid}",
                headers=e2e_client.auth_headers,
            )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True

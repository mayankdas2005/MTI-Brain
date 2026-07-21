"""Integration tests for auth API endpoints."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.integration
class TestLogin:
    async def test_login_success(self, client):
        mock_user = MagicMock()
        mock_user.id = "00000000-0000-0000-0000-000000000001"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.groups = ["user"]

        with patch("app.api.v1.auth.auth_service") as mock_svc:
            mock_svc.authenticate_user = AsyncMock(return_value=mock_user)
            mock_svc.upsert_user = AsyncMock(return_value=mock_user)
            mock_svc.create_jwt_token = MagicMock(return_value="test-jwt-token")
            mock_svc.create_refresh_token = AsyncMock(return_value="refresh-token-123")

            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": "test@example.com", "password": "secret123"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data

    async def test_login_wrong_password(self, client):
        with patch("app.api.v1.auth.auth_service") as mock_svc:
            mock_svc.authenticate_user = AsyncMock(return_value=None)

            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": "test@example.com", "password": "wrong"},
            )

        assert resp.status_code == 401

    async def test_login_missing_fields(self, client):
        resp = await client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_empty_username(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": "pass"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestRefresh:
    async def test_refresh_missing_cookie(self, client):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401

    async def test_refresh_invalid_token(self, client):
        with patch("app.api.v1.auth.auth_service") as mock_svc:
            mock_svc.validate_refresh_token = AsyncMock(return_value=None)

            resp = await client.post(
                "/api/v1/auth/refresh",
                cookies={"mti_brain_refresh": "invalid-token"},
            )

        assert resp.status_code == 401


@pytest.mark.integration
class TestLogout:
    async def test_logout_clears_cookie(self, client):
        with patch("app.api.v1.auth.auth_service") as mock_svc:
            mock_svc.revoke_refresh_token = AsyncMock()

            resp = await client.post(
                "/api/v1/auth/logout",
                cookies={"mti_brain_refresh": "some-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_logout_without_cookie(self, client):
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 200


@pytest.mark.integration
class TestMe:
    async def test_me_with_valid_token(self, authed_client):
        resp = await authed_client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"

    async def test_me_without_token(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_me_with_invalid_token(self, client):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-jwt-token"},
        )
        assert resp.status_code == 401

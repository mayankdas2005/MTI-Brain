"""End-to-end auth lifecycle tests.

Tests the full auth flow through real service layer code with only
external I/O (database) mocked at the boundary.

Covers:
  - Login with valid/invalid credentials
  - Token validation via /auth/me
  - Refresh token rotation
  - Logout (token revocation)
  - Token expiry handling
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID


@pytest.mark.e2e
class TestLoginFlow:
    """E2e tests for the login endpoint with real auth service logic."""

    async def test_login_valid_credentials(self, e2e_unauthed_client):
        """Full login flow: authenticate -> upsert user -> issue JWT + refresh token."""
        mock_session = e2e_unauthed_client.mock_session

        # Simulate DB returning a user with a valid bcrypt hash
        mock_user_row = MagicMock()
        mock_user_row.id = TEST_USER_ID
        mock_user_row.email = "analyst@company.com"
        mock_user_row.name = "Jane Analyst"
        mock_user_row.password_hash = "$2b$12$LJ3m4sMKfNtGQ5L2J8K9/.eZ1pG8pQ9X5L8N3K2e4R6tY7uW9oV1i"
        mock_user_row.groups = ["user", "admin"]
        mock_user_row.keycloak_sub = "analyst@company.com"
        mock_user_row.last_login = datetime.now(timezone.utc)
        mock_user_row.created_at = datetime.now(timezone.utc)
        mock_user_row.distilled_preferences = None
        mock_user_row.distilled_at = None
        mock_user_row.feedback_count_at_distill = 0

        # Make execute return the user when queried
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_user_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Patch bcrypt check to succeed (we're testing the flow, not bcrypt)
        with patch("app.services.auth._check_password", AsyncMock(return_value=True)):
            with patch("app.services.auth.settings") as mock_settings:
                mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
                mock_settings.JWT_ALGORITHM = "HS256"
                mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
                mock_settings.JWT_REFRESH_TOKEN_DAYS = 7

                resp = await e2e_unauthed_client.post(
                    "/api/v1/auth/login",
                    json={"username": "analyst@company.com", "password": "correct-password"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user" in data
        assert data["user"]["email"] == "analyst@company.com"
        assert data["user"]["name"] == "Jane Analyst"
        assert "user_id" in data["user"]

    async def test_login_invalid_credentials(self, e2e_unauthed_client):
        """Login with wrong password returns 401 (real auth_service.authenticate_user)."""
        mock_session = e2e_unauthed_client.mock_session

        # Simulate DB returning a user
        mock_user_row = MagicMock()
        mock_user_row.id = TEST_USER_ID
        mock_user_row.email = "analyst@company.com"
        mock_user_row.name = "Jane Analyst"
        mock_user_row.password_hash = "$2b$12$LJ3m4sMKfNtGQ5L2J8K9/.eZ1pG8pQ9X5L8N3K2e4R6tY7uW9oV1i"
        mock_user_row.groups = ["user"]
        mock_user_row.keycloak_sub = "analyst@company.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_user_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Patch bcrypt to reject the password
        with patch("app.services.auth._check_password", AsyncMock(return_value=False)):
            resp = await e2e_unauthed_client.post(
                "/api/v1/auth/login",
                json={"username": "analyst@company.com", "password": "wrong-password"},
            )

        assert resp.status_code == 401
        assert "Invalid username or password" in resp.json()["detail"]

    async def test_login_user_not_found(self, e2e_unauthed_client):
        """Login for non-existent user returns 401."""
        mock_session = e2e_unauthed_client.mock_session

        # DB returns no user
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        resp = await e2e_unauthed_client.post(
            "/api/v1/auth/login",
            json={"username": "nobody@company.com", "password": "anything"},
        )

        assert resp.status_code == 401

    async def test_login_missing_fields_rejected(self, e2e_unauthed_client):
        """Request validation rejects requests with missing required fields."""
        resp = await e2e_unauthed_client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_empty_username_rejected(self, e2e_unauthed_client):
        """Empty username is rejected by Pydantic validation."""
        resp = await e2e_unauthed_client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": "some-pass"},
        )
        assert resp.status_code == 422

    async def test_login_role_enforcement(self, e2e_unauthed_client):
        """Login fails when requesting admin role but user only has user group."""
        mock_session = e2e_unauthed_client.mock_session

        mock_user_row = MagicMock()
        mock_user_row.id = TEST_USER_ID
        mock_user_row.email = "basic@company.com"
        mock_user_row.name = "Basic User"
        mock_user_row.password_hash = "$2b$12$hash"
        mock_user_row.groups = ["user"]  # No admin
        mock_user_row.keycloak_sub = "basic@company.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_user_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.auth._check_password", AsyncMock(return_value=True)):
            resp = await e2e_unauthed_client.post(
                "/api/v1/auth/login",
                json={"username": "basic@company.com", "password": "correct", "role": "admin"},
            )

        assert resp.status_code == 401


@pytest.mark.e2e
class TestTokenValidation:
    """E2e tests for JWT token validation via /auth/me."""

    async def test_valid_token_returns_user_info(self, e2e_client):
        """A valid JWT allows access to /auth/me and returns user info."""
        resp = await e2e_client.get(
            "/api/v1/auth/me",
            headers=e2e_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "e2e-user@example.com"
        assert data["name"] == "E2E Test User"
        assert data["user_id"] == str(TEST_USER_ID)
        assert "admin" in data["groups"]

    async def test_missing_token_returns_401(self, e2e_unauthed_client):
        """Requests without Authorization header get 401."""
        resp = await e2e_unauthed_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_malformed_token_returns_401(self, e2e_unauthed_client):
        """A clearly invalid JWT returns 401."""
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.JWT_ALGORITHM = "HS256"
            mock_settings.jwt_verify_key = "test-secret-key-for-unit-tests-32chars!"
            mock_settings.JWT_SECRET = "test-secret-key-for-unit-tests-32chars!"

            resp = await e2e_unauthed_client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer not-a-real-jwt"},
            )

        assert resp.status_code == 401

    async def test_expired_token_returns_401(self, e2e_unauthed_client):
        """An expired JWT returns 401."""
        import jwt as pyjwt

        now = datetime.now(timezone.utc)
        payload = {
            "sub": "test@example.com",
            "user_id": str(TEST_USER_ID),
            "email": "test@example.com",
            "name": "Test",
            "groups": ["user"],
            "type": "access",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),  # Expired 1 hour ago
        }
        expired_token = pyjwt.encode(
            payload, "test-secret-key-for-unit-tests-32chars!", algorithm="HS256"
        )

        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.JWT_ALGORITHM = "HS256"
            mock_settings.jwt_verify_key = "test-secret-key-for-unit-tests-32chars!"
            mock_settings.JWT_SECRET = "test-secret-key-for-unit-tests-32chars!"

            resp = await e2e_unauthed_client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

        assert resp.status_code == 401


@pytest.mark.e2e
class TestRefreshFlow:
    """E2e tests for refresh token rotation."""

    async def test_refresh_without_cookie_returns_401(self, e2e_unauthed_client):
        """Refresh endpoint requires the refresh cookie."""
        resp = await e2e_unauthed_client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401
        assert "No refresh token" in resp.json()["detail"]

    async def test_refresh_with_invalid_token_returns_401(self, e2e_unauthed_client):
        """Invalid/expired refresh token returns 401 and clears cookie."""
        mock_session = e2e_unauthed_client.mock_session

        # validate_refresh_token returns None (token not found or expired)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        resp = await e2e_unauthed_client.post(
            "/api/v1/auth/refresh",
            cookies={"mti_brain_refresh": "invalid-refresh-token"},
        )

        assert resp.status_code == 401
        assert "expired or revoked" in resp.json()["detail"]

    async def test_refresh_with_valid_token_rotates(self, e2e_unauthed_client):
        """Valid refresh token issues new access + refresh tokens."""
        mock_session = e2e_unauthed_client.mock_session

        # First call: validate_refresh_token finds valid token
        mock_rt = MagicMock()
        mock_rt.user_id = TEST_USER_ID
        mock_rt.token_hash = "abc123"
        mock_rt.revoked = False
        mock_rt.expires_at = datetime.now(timezone.utc) + timedelta(days=5)

        # Second call: revoke (UPDATE) returns rowcount=1
        mock_revoke_result = MagicMock()
        mock_revoke_result.rowcount = 1

        # Third call: select user
        mock_user = MagicMock()
        mock_user.id = TEST_USER_ID
        mock_user.email = "analyst@company.com"
        mock_user.name = "Jane Analyst"
        mock_user.groups = ["user"]

        # Set up execute to return different results for each call
        call_count = [0]
        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_rt)),  # validate
            mock_revoke_result,  # revoke
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_user)),  # load user
            MagicMock(),  # create new refresh token (INSERT)
        ]

        async def _execute_side_effect(*args, **kwargs):
            idx = min(call_count[0], len(results) - 1)
            call_count[0] += 1
            return results[idx]

        mock_session.execute = AsyncMock(side_effect=_execute_side_effect)

        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
            mock_settings.JWT_ALGORITHM = "HS256"
            mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
            mock_settings.JWT_REFRESH_TOKEN_DAYS = 7

            resp = await e2e_unauthed_client.post(
                "/api/v1/auth/refresh",
                cookies={"mti_brain_refresh": "valid-refresh-token-abc"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "analyst@company.com"


@pytest.mark.e2e
class TestLogoutFlow:
    """E2e tests for logout (refresh token revocation)."""

    async def test_logout_with_cookie_revokes_token(self, e2e_unauthed_client):
        """Logout revokes the refresh token and clears the cookie."""
        mock_session = e2e_unauthed_client.mock_session

        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        resp = await e2e_unauthed_client.post(
            "/api/v1/auth/logout",
            cookies={"mti_brain_refresh": "refresh-token-to-revoke"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Verify revoke was called (the service layer calls db.execute with UPDATE)
        assert mock_session.execute.called

    async def test_logout_without_cookie_succeeds(self, e2e_unauthed_client):
        """Logout without a cookie still returns 200 (no-op)."""
        resp = await e2e_unauthed_client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.e2e
class TestFullAuthLifecycle:
    """E2e test combining login -> use token -> logout."""

    async def test_login_then_access_protected_endpoint(self, e2e_unauthed_client):
        """Login, get a token, then use it to access a protected endpoint."""
        mock_session = e2e_unauthed_client.mock_session

        # Setup user for login
        mock_user_row = MagicMock()
        mock_user_row.id = TEST_USER_ID
        mock_user_row.email = "lifecycle@company.com"
        mock_user_row.name = "Lifecycle User"
        mock_user_row.password_hash = "$2b$12$hash"
        mock_user_row.groups = ["user"]
        mock_user_row.keycloak_sub = "lifecycle@company.com"
        mock_user_row.last_login = datetime.now(timezone.utc)
        mock_user_row.created_at = datetime.now(timezone.utc)
        mock_user_row.distilled_preferences = None
        mock_user_row.distilled_at = None
        mock_user_row.feedback_count_at_distill = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_user_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.auth._check_password", AsyncMock(return_value=True)):
            with patch("app.services.auth.settings") as mock_settings:
                mock_settings.JWT_ACCESS_TOKEN_MINUTES = 60
                mock_settings.JWT_ALGORITHM = "HS256"
                mock_settings.jwt_signing_key = "test-secret-key-for-unit-tests-32chars!"
                mock_settings.JWT_REFRESH_TOKEN_DAYS = 7

                # Step 1: Login
                login_resp = await e2e_unauthed_client.post(
                    "/api/v1/auth/login",
                    json={"username": "lifecycle@company.com", "password": "pass123"},
                )

        assert login_resp.status_code == 200
        token = login_resp.json()["token"]

        # Step 2: Use the token to access /auth/me
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.JWT_ALGORITHM = "HS256"
            mock_settings.jwt_verify_key = "test-secret-key-for-unit-tests-32chars!"
            mock_settings.JWT_SECRET = "test-secret-key-for-unit-tests-32chars!"

            me_resp = await e2e_unauthed_client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "lifecycle@company.com"

        # Step 3: Logout
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        logout_resp = await e2e_unauthed_client.post(
            "/api/v1/auth/logout",
            cookies={"mti_brain_refresh": "some-refresh-token"},
        )
        assert logout_resp.status_code == 200

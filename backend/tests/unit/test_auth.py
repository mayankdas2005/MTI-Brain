"""Unit tests for services/auth — pure function tests (no DB)."""

import hashlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.services.auth import (
    _hash_token,
    _role_allowed,
    create_jwt_token,
    decode_jwt_token,
)


class TestRoleAllowed:
    def test_role_in_groups(self):
        assert _role_allowed(["admin", "user"], "admin") is True

    def test_role_not_in_groups(self):
        assert _role_allowed(["user"], "admin") is False

    def test_none_groups(self):
        assert _role_allowed(None, "admin") is False

    def test_empty_groups(self):
        assert _role_allowed([], "user") is False

    def test_exact_match_required(self):
        assert _role_allowed(["administrator"], "admin") is False


class TestHashToken:
    def test_deterministic(self):
        token = "test-token-abc"
        assert _hash_token(token) == _hash_token(token)

    def test_sha256_format(self):
        result = _hash_token("any-token")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_matches_stdlib(self):
        token = "verify-this"
        expected = hashlib.sha256(token.encode()).hexdigest()
        assert _hash_token(token) == expected


class TestCreateJwtToken:
    @patch("app.services.auth.settings")
    def test_creates_valid_token(self, mock_settings):
        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 15
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "test-secret-key"
        mock_settings.jwt_verify_key = "test-secret-key"
        mock_settings.JWT_SECRET = "test-secret-key"

        token = create_jwt_token("user-1", "test@example.com", "Test User", ["user"])
        assert isinstance(token, str)
        assert len(token) > 0

    @patch("app.services.auth.settings")
    def test_token_contains_claims(self, mock_settings):
        import jwt as pyjwt

        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 15
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "secret"
        mock_settings.jwt_verify_key = "secret"

        token = create_jwt_token("user-1", "test@example.com", "Test User", ["admin"])
        payload = pyjwt.decode(token, "secret", algorithms=["HS256"])
        assert payload["sub"] == "test@example.com"
        assert payload["user_id"] == "user-1"
        assert payload["email"] == "test@example.com"
        assert payload["name"] == "Test User"
        assert payload["groups"] == ["admin"]
        assert payload["type"] == "access"
        assert "iat" in payload
        assert "exp" in payload


class TestDecodeJwtToken:
    @patch("app.services.auth.settings")
    def test_valid_token(self, mock_settings):
        mock_settings.JWT_ACCESS_TOKEN_MINUTES = 15
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_signing_key = "secret"
        mock_settings.jwt_verify_key = "secret"
        mock_settings.JWT_SECRET = "secret"

        token = create_jwt_token("u1", "a@b.com", "A", ["user"])
        payload = decode_jwt_token(token)
        assert payload is not None
        assert payload["email"] == "a@b.com"

    @patch("app.services.auth.settings")
    def test_expired_token(self, mock_settings):
        import jwt as pyjwt

        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_verify_key = "secret"
        mock_settings.JWT_SECRET = "secret"

        expired_payload = {
            "sub": "x@y.com",
            "exp": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "iat": datetime(2020, 1, 1, tzinfo=timezone.utc),
        }
        token = pyjwt.encode(expired_payload, "secret", algorithm="HS256")
        assert decode_jwt_token(token) is None

    @patch("app.services.auth.settings")
    def test_tampered_token(self, mock_settings):
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_verify_key = "correct-secret"
        mock_settings.JWT_SECRET = "correct-secret"

        import jwt as pyjwt
        token = pyjwt.encode({"sub": "x"}, "wrong-secret", algorithm="HS256")
        assert decode_jwt_token(token) is None

    @patch("app.services.auth.settings")
    def test_malformed_token(self, mock_settings):
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.jwt_verify_key = "secret"
        mock_settings.JWT_SECRET = "secret"

        assert decode_jwt_token("not.a.valid.jwt") is None
        assert decode_jwt_token("") is None

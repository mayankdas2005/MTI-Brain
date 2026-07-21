"""Unit tests for core/rate_limit — _get_real_ip, _get_user_id."""

import pytest
from unittest.mock import MagicMock, patch

from app.core.rate_limit import _get_real_ip, _get_user_id


def _mock_request(headers=None, client_host="192.168.1.1"):
    request = MagicMock()
    _headers = headers or {}
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: _headers.get(key, default)
    request.client = MagicMock()
    request.client.host = client_host
    return request


class TestGetRealIp:
    def test_x_real_ip_header(self):
        req = _mock_request(headers={"X-Real-IP": "10.0.0.1"})
        assert _get_real_ip(req) == "10.0.0.1"

    def test_x_forwarded_for_first(self):
        req = _mock_request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        assert _get_real_ip(req) == "1.2.3.4"

    def test_direct_client_ip(self):
        req = _mock_request(client_host="203.0.113.50")
        assert _get_real_ip(req) == "203.0.113.50"

    def test_no_client_fallback(self):
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default=None: None
        req.client = None
        assert _get_real_ip(req) == "127.0.0.1"

    def test_x_real_ip_priority_over_forwarded_for(self):
        req = _mock_request(headers={
            "X-Real-IP": "10.0.0.1",
            "X-Forwarded-For": "1.2.3.4",
        })
        assert _get_real_ip(req) == "10.0.0.1"


class TestGetUserId:
    @patch("app.services.auth.decode_jwt_token")
    def test_valid_token(self, mock_decode):
        mock_decode.return_value = {"user_id": "usr-123", "email": "a@b.com"}
        req = _mock_request(headers={"Authorization": "Bearer valid-token"})
        result = _get_user_id(req)
        assert result == "user:usr-123"

    @patch("app.services.auth.decode_jwt_token")
    def test_invalid_token_falls_back_to_ip(self, mock_decode):
        mock_decode.return_value = None
        req = _mock_request(headers={"Authorization": "Bearer bad"}, client_host="5.5.5.5")
        result = _get_user_id(req)
        assert result == "5.5.5.5"

    def test_no_auth_header(self):
        req = _mock_request(client_host="1.1.1.1")
        result = _get_user_id(req)
        assert result == "1.1.1.1"

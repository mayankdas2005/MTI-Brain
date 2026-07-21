"""Unit tests for core/retry — is_transient() classification."""

import pytest

from app.core.retry import is_transient


class TestIsTransient:
    @pytest.mark.parametrize("msg", [
        "connection timeout expired",
        "Connection timed out after 30s",
        "connection reset by peer",
        "Connection refused to host",
        "broken pipe",
        "socket closed unexpectedly",
        "EOF occurred in violation of protocol",
        "connection forcibly closed",
        "ConnectionReset: peer closed",
        "ConnectionRefused on port 5432",
        "ServiceUnavailable: too many requests",
        "SessionExpired: please re-authenticate",
        "pool exhausted: no connections available",
        "failed to read from socket",
        "failed to write to connection",
        "network is unreachable",
        "SSL: CERTIFICATE_VERIFY_FAILED",
        "error 10054 connection reset",
        "error 10061 connection refused",
        "server closed the connection unexpectedly",
        "server terminated abnormally",
        "connection closed before response",
    ])
    def test_transient_errors(self, msg):
        exc = Exception(msg)
        assert is_transient(exc) is True

    @pytest.mark.parametrize("msg", [
        "syntax error at or near 'SELECT'",
        "permission denied for table users",
        "relation 'lpp.nonexistent' does not exist",
        "column 'foo' does not exist",
        "division by zero",
        "invalid input syntax for type integer",
        "duplicate key value violates unique constraint",
        "value too long for type character varying(255)",
        "cannot drop table because other objects depend on it",
    ])
    def test_non_transient_errors(self, msg):
        exc = Exception(msg)
        assert is_transient(exc) is False

    def test_empty_message(self):
        assert is_transient(Exception("")) is False

    def test_case_insensitive(self):
        assert is_transient(Exception("CONNECTION TIMEOUT")) is True
        assert is_transient(Exception("BROKEN PIPE")) is True

    def test_nested_exception_message(self):
        inner = ConnectionError("connection reset by peer")
        assert is_transient(inner) is True

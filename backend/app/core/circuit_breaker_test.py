"""Unit tests for core/circuit_breaker — LoggingListener, breaker instances."""

import pytest
from unittest.mock import MagicMock, patch

from app.core.circuit_breaker import (
    LoggingListener,
    llm_breaker,
    neo4j_breaker,
    redis_breaker,
    external_api_breaker,
)


class TestLoggingListener:
    def setup_method(self):
        self.listener = LoggingListener()
        self.cb = MagicMock()
        self.cb.name = "test_breaker"

    @patch("app.core.circuit_breaker.logger")
    def test_state_change_logs_warning(self, mock_logger):
        old_state = MagicMock()
        old_state.name = "closed"
        new_state = MagicMock()
        new_state.name = "open"
        self.listener.state_change(self.cb, old_state, new_state)
        mock_logger.warning.assert_called_once()
        call_args = str(mock_logger.warning.call_args)
        assert "test_breaker" in call_args

    @patch("app.core.circuit_breaker.logger")
    def test_failure_logs_error(self, mock_logger):
        exc = ValueError("connection timeout")
        self.listener.failure(self.cb, exc)
        mock_logger.error.assert_called_once()

    @patch("app.core.circuit_breaker.logger")
    def test_success_logs_debug(self, mock_logger):
        self.listener.success(self.cb)
        mock_logger.debug.assert_called_once()


class TestBreakerInstances:
    def test_llm_breaker_exists(self):
        assert llm_breaker is not None
        assert llm_breaker.fail_max == 3

    def test_neo4j_breaker_exists(self):
        assert neo4j_breaker is not None

    def test_redis_breaker_exists(self):
        assert redis_breaker is not None
        assert redis_breaker.fail_max == 10

    def test_external_api_breaker_exists(self):
        assert external_api_breaker is not None
        assert external_api_breaker.fail_max == 3

    def test_breakers_have_listener(self):
        for breaker in [llm_breaker, neo4j_breaker, redis_breaker]:
            assert len(breaker.listeners) > 0
            assert any(isinstance(l, LoggingListener) for l in breaker.listeners)

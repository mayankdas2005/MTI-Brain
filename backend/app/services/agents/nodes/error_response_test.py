"""Unit tests for nodes/error_response — error code mapping."""

import pytest

from app.services.agents.nodes.error_response import _ERROR_MESSAGES, error_response


class TestErrorMessages:
    def test_all_known_codes_have_messages(self):
        expected_codes = ["semantic_layer_unavailable", "data_unavailable", "llm_unavailable", "default"]
        for code in expected_codes:
            assert code in _ERROR_MESSAGES
            assert len(_ERROR_MESSAGES[code]) > 10

    def test_messages_are_user_friendly(self):
        for code, msg in _ERROR_MESSAGES.items():
            assert "traceback" not in msg.lower()
            assert "exception" not in msg.lower()
            assert "stacktrace" not in msg.lower()


class TestErrorResponseNode:
    @pytest.mark.asyncio
    async def test_known_error_code(self):
        state = {"thread_id": "t1", "error": "llm_unavailable"}
        result = await error_response(state, config={})
        assert result["answer"] == _ERROR_MESSAGES["llm_unavailable"]
        assert result["stopped"] is True
        assert result["follow_ups"] == []

    @pytest.mark.asyncio
    async def test_unknown_error_code_uses_default(self):
        state = {"thread_id": "t1", "error": "some_random_error"}
        result = await error_response(state, config={})
        assert result["answer"] == _ERROR_MESSAGES["default"]

    @pytest.mark.asyncio
    async def test_missing_error_uses_default(self):
        state = {"thread_id": "t1"}
        result = await error_response(state, config={})
        assert result["answer"] == _ERROR_MESSAGES["default"]

    @pytest.mark.asyncio
    async def test_semantic_layer_unavailable(self):
        state = {"thread_id": "t1", "error": "semantic_layer_unavailable"}
        result = await error_response(state, config={})
        assert "data catalog" in result["answer"]

    @pytest.mark.asyncio
    async def test_data_unavailable(self):
        state = {"thread_id": "t1", "error": "data_unavailable"}
        result = await error_response(state, config={})
        assert "data warehouse" in result["answer"]

"""Unit tests for nodes/compress — _message_text, _build_exchanges, SUMMARIZE_THRESHOLD."""

import pytest
from unittest.mock import MagicMock

from app.services.agents.nodes.compress import (
    SUMMARIZE_THRESHOLD,
    _message_text,
    _build_exchanges,
)


class TestSummarizeThreshold:
    def test_is_positive_int(self):
        assert isinstance(SUMMARIZE_THRESHOLD, int)
        assert SUMMARIZE_THRESHOLD > 0


class TestMessageText:
    def test_string_content(self):
        msg = MagicMock()
        msg.content = "Hello world"
        assert _message_text(msg) == "Hello world"

    def test_list_content(self):
        msg = MagicMock()
        msg.content = [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]
        result = _message_text(msg)
        assert "Part 1" in result
        assert "Part 2" in result

    def test_empty_string(self):
        msg = MagicMock()
        msg.content = ""
        assert _message_text(msg) == ""

    def test_none_content(self):
        msg = MagicMock()
        msg.content = None
        result = _message_text(msg)
        assert isinstance(result, str)


class TestBuildExchanges:
    def test_empty_list(self):
        assert _build_exchanges([]) == []

    def test_human_message(self):
        from langchain_core.messages import HumanMessage
        msgs = [HumanMessage(content="What is revenue?")]
        result = _build_exchanges(msgs)
        assert len(result) == 1
        assert result[0].startswith("Q: ")

    def test_ai_message(self):
        from langchain_core.messages import AIMessage
        msgs = [AIMessage(content="Revenue was $1M.")]
        result = _build_exchanges(msgs)
        assert len(result) == 1
        assert result[0].startswith("A: ")

    def test_truncates_long_messages(self):
        from langchain_core.messages import HumanMessage
        long_text = "x" * 1000
        msgs = [HumanMessage(content=long_text)]
        result = _build_exchanges(msgs)
        assert len(result[0]) <= 410  # "Q: " + 400 + "..."

    def test_skips_empty_messages(self):
        from langchain_core.messages import HumanMessage, AIMessage
        msgs = [HumanMessage(content=""), AIMessage(content="answer")]
        result = _build_exchanges(msgs)
        assert len(result) == 1
        assert result[0].startswith("A: ")

"""Unit tests for token_tracker — calculate_cost, extract_usage, aggregate."""

import pytest
from unittest.mock import MagicMock

from app.services.agents.token_tracker import (
    MODELS,
    _get_pricing,
    calculate_cost,
    extract_usage,
    aggregate_token_usage,
)


class TestGetPricing:
    def test_known_model(self):
        pricing = _get_pricing("claude-sonnet-4-6")
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] > 0

    def test_unknown_model_returns_default(self):
        pricing = _get_pricing("totally-unknown-model-xyz")
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_case_insensitive(self):
        p1 = _get_pricing("claude-haiku-4-5")
        p2 = _get_pricing("Claude-Haiku-4-5")
        assert p1 == p2

    def test_underscore_normalized(self):
        p1 = _get_pricing("claude_sonnet_4_6")
        p2 = _get_pricing("claude-sonnet-4-6")
        assert p1 == p2


class TestCalculateCost:
    def test_basic_cost(self):
        cost = calculate_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
        assert cost > 0
        assert isinstance(cost, float)

    def test_zero_tokens(self):
        cost = calculate_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_cache_tokens_included(self):
        cost_no_cache = calculate_cost("claude-sonnet-4-6", 1000, 500)
        cost_with_cache = calculate_cost("claude-sonnet-4-6", 1000, 500, cache_creation_tokens=1000)
        assert cost_with_cache > cost_no_cache

    def test_cache_read_cheaper_than_write(self):
        cost_write = calculate_cost("claude-sonnet-4-6", 0, 0, cache_creation_tokens=1000)
        cost_read = calculate_cost("claude-sonnet-4-6", 0, 0, cache_read_tokens=1000)
        assert cost_read < cost_write

    def test_rounded_to_8_decimals(self):
        cost = calculate_cost("claude-sonnet-4-6", 1, 1)
        decimal_places = len(str(cost).split(".")[-1]) if "." in str(cost) else 0
        assert decimal_places <= 8


class TestExtractUsage:
    def _make_msg(self, input_tokens=100, output_tokens=50, model="claude-sonnet-4-6"):
        msg = MagicMock()
        msg.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_creation": 0, "cache_read": 0},
        }
        msg.response_metadata = {"model_name": model}
        return msg

    def test_basic_extraction(self):
        msg = self._make_msg(100, 50)
        result = extract_usage(msg, "intake", "balanced")
        assert result is not None
        assert result["node"] == "intake"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["cost_usd"] > 0

    def test_zero_tokens_returns_none(self):
        msg = self._make_msg(0, 0)
        result = extract_usage(msg, "test", "fast")
        assert result is None

    def test_missing_usage_metadata(self):
        msg = MagicMock()
        msg.usage_metadata = None
        msg.response_metadata = {}
        result = extract_usage(msg, "test", "fast")
        assert result is None

    def test_model_name_extracted(self):
        msg = self._make_msg(100, 50, model="claude-haiku-4-5")
        result = extract_usage(msg, "intake", "fast")
        assert result is not None
        assert "haiku" in result["model"].lower()


class TestAggregateTokenUsage:
    def test_empty_records(self):
        assert aggregate_token_usage([]) == {}

    def test_single_record(self):
        records = [{
            "node": "intake",
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.001,
        }]
        result = aggregate_token_usage(records)
        assert result["total_input_tokens"] == 100
        assert result["total_output_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["total_cost_usd"] == 0.001

    def test_multiple_records_summed(self):
        records = [
            {"node": "a", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
             "cache_creation_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.001},
            {"node": "b", "input_tokens": 200, "output_tokens": 100, "total_tokens": 300,
             "cache_creation_tokens": 10, "cache_read_tokens": 20, "cost_usd": 0.002},
        ]
        result = aggregate_token_usage(records)
        assert result["total_input_tokens"] == 300
        assert result["total_output_tokens"] == 150
        assert result["total_tokens"] == 450
        assert result["cache_creation_tokens"] == 10
        assert result["cache_read_tokens"] == 20
        assert result["by_node"] == records

"""Unit tests for filter_resolver_logic — fuzzy match tiers + temporal resolution."""

import pytest

from app.services.agents.filter_resolver_logic import (
    _extract_segments,
    resolve_to_patterns,
    resolve_tier1_combined,
    resolve_tier1_exact,
    resolve_tier3_temporal,
)


class TestExtractSegments:
    def test_underscore_separated(self):
        result = _extract_segments("GR_AE_OPERATING_1")
        assert "OPERATING" in result
        assert "1" not in result  # purely numeric
        assert "GR" not in result  # < 3 chars
        assert "AE" not in result  # < 3 chars

    def test_camel_case(self):
        result = _extract_segments("CashInflows")
        assert "CASH" in result
        assert "INFLOWS" in result

    def test_dash_separated(self):
        result = _extract_segments("fx-rate-daily")
        assert "RATE" in result
        assert "DAILY" in result
        assert "FX" not in result  # < 3 chars

    def test_dot_separated(self):
        result = _extract_segments("net.income.total")
        assert "NET" in result
        assert "INCOME" in result
        assert "TOTAL" in result

    def test_empty_string(self):
        assert _extract_segments("") == []

    def test_short_token_only(self):
        assert _extract_segments("AB") == []

    def test_deduplication(self):
        result = _extract_segments("Test_Test_Test")
        assert result.count("TEST") == 1

    def test_mixed_separators(self):
        result = _extract_segments("cash_flow/net-income")
        assert "CASH" in result
        assert "FLOW" in result
        assert "NET" in result  # 3 chars passes the filter
        assert "INCOME" in result


class TestResolveToPatterns:
    def test_alias_exact_match(self):
        aliases = {"US_001": "United States"}
        patterns, score = resolve_to_patterns("United States", ["US_001", "UK_002"], aliases)
        assert patterns == ["US_001"]
        assert score == 100.0

    def test_alias_forward_match(self):
        aliases = {"US_001": "United States"}
        patterns, score = resolve_to_patterns("US_001", ["US_001", "UK_002"], aliases)
        assert patterns == ["US_001"]
        assert score == 100.0

    def test_exact_vocabulary_match(self):
        patterns, score = resolve_to_patterns("operating", ["Operating", "Capital", "Revenue"], None)
        assert patterns == ["Operating"]
        assert score == 100.0

    def test_case_insensitive_exact(self):
        patterns, score = resolve_to_patterns("REVENUE", ["Revenue", "Cost"], None)
        assert patterns == ["Revenue"]
        assert score == 100.0

    def test_substring_match(self):
        patterns, score = resolve_to_patterns("cash", ["CashInflows", "CashOutflows", "Revenue"], None)
        assert score == 65.0

    def test_no_match(self):
        patterns, score = resolve_to_patterns("nonexistent", ["Alpha", "Beta", "Gamma"], None)
        assert patterns == []
        assert score == 0.0

    def test_empty_filter_values(self):
        patterns, score = resolve_to_patterns("anything", [], None)
        assert patterns == []
        assert score == 0.0

    def test_too_many_filter_values(self):
        patterns, score = resolve_to_patterns("x", ["v"] * 501, None)
        assert patterns == []
        assert score == 0.0


class TestResolveTier1Combined:
    def test_alias_reverse_lookup(self):
        aliases = {"GR_US_INC": "US Income"}
        val, score, candidates = resolve_tier1_combined("US Income", ["GR_US_INC"], aliases)
        assert val == "GR_US_INC"
        assert score == 100.0
        assert candidates == []

    def test_exact_case_insensitive(self):
        val, score, candidates = resolve_tier1_combined("revenue", ["Revenue", "Cost"], None)
        assert val == "Revenue"
        assert score == 100.0

    def test_substring_hit(self):
        val, score, candidates = resolve_tier1_combined("operating", ["GR_US_OPERATING_1", "Other"], None)
        assert score == 65.0

    def test_no_match_returns_none(self):
        val, score, candidates = resolve_tier1_combined("zzz", ["Alpha", "Beta"], None)
        assert val is None or score < 70


class TestResolveTier1Exact:
    def test_alias_match(self):
        aliases = {"DB_CODE": "Human Label"}
        result = resolve_tier1_exact("DB_CODE", [], aliases)
        assert result == "Human Label"

    def test_sample_value_match(self):
        result = resolve_tier1_exact("revenue", ["Revenue", "Cost"], None)
        assert result == "Revenue"

    def test_no_match(self):
        result = resolve_tier1_exact("nonexistent", ["Alpha", "Beta"], None)
        assert result is None


class TestResolveTier3Temporal:
    def test_today(self):
        result = resolve_tier3_temporal("today")
        assert result is not None
        assert result["operator"] == "="
        assert result["value"] == "CURRENT_DATE"
        assert result["is_raw_sql"] is True

    def test_yesterday(self):
        result = resolve_tier3_temporal("yesterday")
        assert result is not None
        assert "DATEADD" in result["value"]

    def test_iso_date(self):
        result = resolve_tier3_temporal("2024-01-15")
        assert result is not None
        assert result["operator"] == "="
        assert result["value"] == "2024-01-15"

    def test_date_range(self):
        result = resolve_tier3_temporal("2024-01-01 to 2024-03-31")
        assert result is not None
        assert result["operator"] == "BETWEEN"
        assert result["value"] == ["2024-01-01", "2024-03-31"]

    def test_last_30_days(self):
        result = resolve_tier3_temporal("last 30 days")
        assert result is not None
        assert result["operator"] == "BETWEEN_SQL"
        assert result["is_raw_sql"] is True

    def test_this_year(self):
        result = resolve_tier3_temporal("this year")
        assert result is not None
        assert result["operator"] == "BETWEEN_SQL"

    def test_ytd(self):
        result = resolve_tier3_temporal("YTD")
        assert result is not None

    def test_next_90_days(self):
        result = resolve_tier3_temporal("next 90 days")
        assert result is not None
        assert result["operator"] == "BETWEEN_SQL"

    def test_unrecognized_returns_none(self):
        result = resolve_tier3_temporal("sometime last spring")
        assert result is None

    def test_last_quarter(self):
        result = resolve_tier3_temporal("last quarter")
        assert result is not None
        assert "quarter" in result["value"][0].lower()

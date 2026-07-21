"""Unit tests for nodes/schema_gap_resolver — _parse_gaps, _split_concept."""

import pytest

from app.services.agents.nodes.schema_gap_resolver import _parse_gaps, _split_concept


class TestParseGaps:
    def test_join_directive(self):
        text = "SCHEMA_GAP_JOIN: lpp.orders | lpp.items"
        join_pairs, tables, concepts = _parse_gaps(text)
        assert join_pairs == [("lpp.orders", "lpp.items")]
        assert tables == []
        assert concepts == []

    def test_table_directive(self):
        text = "SCHEMA_GAP_TABLE: lpp.customers"
        join_pairs, tables, concepts = _parse_gaps(text)
        assert tables == ["lpp.customers"]
        assert join_pairs == []

    def test_concept_directive(self):
        text = "SCHEMA_GAP_CONCEPT: net_income | The net income after taxes"
        join_pairs, tables, concepts = _parse_gaps(text)
        assert concepts == ["net_income | The net income after taxes"]
        assert join_pairs == []
        assert tables == []

    def test_multiple_directives(self):
        text = """SCHEMA_GAP_JOIN: lpp.a | lpp.b
SCHEMA_GAP_TABLE: lpp.c
SCHEMA_GAP_CONCEPT: revenue | Total revenue
SCHEMA_GAP_JOIN: lpp.d | lpp.e"""
        join_pairs, tables, concepts = _parse_gaps(text)
        assert len(join_pairs) == 2
        assert len(tables) == 1
        assert len(concepts) == 1

    def test_empty_string(self):
        join_pairs, tables, concepts = _parse_gaps("")
        assert join_pairs == []
        assert tables == []
        assert concepts == []

    def test_non_directive_lines_ignored(self):
        text = """Some random text
SCHEMA_GAP_TABLE: lpp.t
More random text"""
        _, tables, _ = _parse_gaps(text)
        assert tables == ["lpp.t"]

    def test_malformed_join_ignored(self):
        text = "SCHEMA_GAP_JOIN: only_one_table"
        join_pairs, _, _ = _parse_gaps(text)
        assert join_pairs == []


class TestSplitConcept:
    def test_pipe_separated(self):
        key, desc = _split_concept("net_income | The net income after taxes")
        assert key == "net_income"
        assert desc == "The net income after taxes"

    def test_no_pipe_uses_first_token(self):
        key, desc = _split_concept("revenue total company revenue metric")
        assert key == "revenue"
        assert "total" in desc

    def test_pipe_with_spaces(self):
        key, desc = _split_concept("  cash_flow  |  Operating cash flow  ")
        assert key == "cash_flow"
        assert desc == "Operating cash flow"

    def test_single_word(self):
        key, desc = _split_concept("revenue")
        assert key == "revenue"

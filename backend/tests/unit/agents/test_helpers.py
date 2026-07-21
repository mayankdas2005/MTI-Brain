"""Unit tests for helpers — merge_neo4j_raw_graph, render_filter_value, format_sql."""

import pytest

from app.services.agents.helpers import (
    merge_neo4j_raw_graph,
    render_filter_value,
    format_sql,
)


class TestMergeNeo4jRawGraph:
    def test_empty_existing(self):
        result = merge_neo4j_raw_graph(
            {},
            [{"_label": "Table", "fqn": "lpp.orders"}],
            [{"_type": "HAS_COLUMN", "table_fqn": "lpp.orders", "column_name": "id"}],
        )
        assert len(result["nodes"]) == 1
        assert len(result["edges"]) == 1

    def test_deduplicates_nodes(self):
        existing = {"nodes": [{"_label": "Table", "fqn": "lpp.orders"}], "edges": []}
        result = merge_neo4j_raw_graph(
            existing,
            [{"_label": "Table", "fqn": "lpp.orders"}],
            [],
        )
        assert len(result["nodes"]) == 1

    def test_deduplicates_edges(self):
        existing = {
            "nodes": [],
            "edges": [{"_type": "HAS_COLUMN", "table_fqn": "lpp.orders", "column_name": "id"}],
        }
        result = merge_neo4j_raw_graph(
            existing,
            [],
            [{"_type": "HAS_COLUMN", "table_fqn": "lpp.orders", "column_name": "id"}],
        )
        assert len(result["edges"]) == 1

    def test_adds_new_nodes(self):
        existing = {"nodes": [{"_label": "Table", "fqn": "lpp.orders"}], "edges": []}
        result = merge_neo4j_raw_graph(
            existing,
            [{"_label": "Table", "fqn": "lpp.items"}],
            [],
        )
        assert len(result["nodes"]) == 2

    def test_column_node_key(self):
        existing = {
            "nodes": [{"_label": "Column", "table_fqn": "lpp.t", "name": "id"}],
            "edges": [],
        }
        result = merge_neo4j_raw_graph(
            existing,
            [{"_label": "Column", "table_fqn": "lpp.t", "name": "id"}],
            [],
        )
        assert len(result["nodes"]) == 1

    def test_different_column_added(self):
        existing = {
            "nodes": [{"_label": "Column", "table_fqn": "lpp.t", "name": "id"}],
            "edges": [],
        }
        result = merge_neo4j_raw_graph(
            existing,
            [{"_label": "Column", "table_fqn": "lpp.t", "name": "amount"}],
            [],
        )
        assert len(result["nodes"]) == 2

    def test_none_new_nodes(self):
        existing = {"nodes": [{"_label": "Table", "fqn": "lpp.t"}], "edges": []}
        result = merge_neo4j_raw_graph(existing, None, None)
        assert len(result["nodes"]) == 1
        assert len(result["edges"]) == 0

    def test_joins_to_edge_key(self):
        edge = {"_type": "JOINS_TO", "from_fqn": "a", "to_fqn": "b", "from_col": "x", "to_col": "y"}
        existing = {"nodes": [], "edges": [edge]}
        result = merge_neo4j_raw_graph(existing, [], [edge.copy()])
        assert len(result["edges"]) == 1


class TestRenderFilterValue:
    def test_between_list(self):
        result = render_filter_value("BETWEEN", ["2024-01-01", "2024-03-31"])
        assert result == "BETWEEN 2024-01-01 AND 2024-03-31"

    def test_between_sql(self):
        result = render_filter_value("BETWEEN_SQL", ["CURRENT_DATE", "DATEADD(day,90,CURRENT_DATE)"])
        assert result == "BETWEEN CURRENT_DATE AND DATEADD(day,90,CURRENT_DATE)"

    def test_between_single_value(self):
        result = render_filter_value("BETWEEN", "2024-01-01")
        assert "BETWEEN 2024-01-01 AND 2024-01-01" == result

    def test_in_list(self):
        result = render_filter_value("IN", ["USD", "EUR"])
        assert result == "IN ('USD', 'EUR')"

    def test_in_single_value(self):
        result = render_filter_value("IN", "USD")
        assert result == "IN ('USD')"

    def test_list_value_becomes_in(self):
        result = render_filter_value("=", ["a", "b"])
        assert result == "IN ('a', 'b')"

    def test_ilike(self):
        result = render_filter_value("ILIKE", "%cash%")
        assert result == "ILIKE '%cash%'"

    def test_like(self):
        result = render_filter_value("LIKE", "%revenue%")
        assert result == "LIKE '%revenue%'"

    def test_comparison_operator(self):
        result = render_filter_value(">=", "DATEADD(day,-60,CURRENT_DATE)")
        assert result == ">= DATEADD(day,-60,CURRENT_DATE)"

    def test_equals(self):
        result = render_filter_value("=", "active")
        assert result == "= active"

    def test_none_operator_defaults(self):
        result = render_filter_value(None, "test")
        assert "test" in result


class TestFormatSql:
    def test_simple_select(self):
        sql = "SELECT id, name FROM lpp.users WHERE id = 1"
        result = format_sql(sql)
        assert "SELECT" in result
        assert "FROM" in result

    def test_empty_string(self):
        assert format_sql("") == ""
        assert format_sql("   ") == "   "

    def test_invalid_sql_returns_stripped(self):
        sql = "))) completely ((( unparseable %%% garbage"
        result = format_sql(sql)
        assert result == sql.strip()

    def test_preserves_semantics(self):
        sql = "SELECT a, SUM(b) FROM lpp.t GROUP BY a"
        result = format_sql(sql)
        assert "SUM" in result.upper()
        assert "GROUP BY" in result.upper()

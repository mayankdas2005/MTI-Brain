"""Unit tests for services/analysis/sql — extract_source_tables."""

import pytest

from app.services.analysis.sql import extract_source_tables


class TestExtractSourceTables:
    def test_simple_select(self):
        sql = "SELECT id, name FROM lpp.orders"
        result = extract_source_tables(sql)
        assert "lpp.orders" in result

    def test_multiple_tables(self):
        sql = "SELECT a.id, b.name FROM lpp.orders a JOIN lpp.items b ON a.id = b.order_id"
        result = extract_source_tables(sql)
        assert "lpp.orders" in result
        assert "lpp.items" in result

    def test_excludes_ctes(self):
        sql = """
        WITH cte AS (SELECT id FROM lpp.orders)
        SELECT id FROM cte
        """
        result = extract_source_tables(sql)
        assert "lpp.orders" in result
        assert "cte" not in result

    def test_multiple_ctes(self):
        sql = """
        WITH a AS (SELECT id FROM lpp.orders),
             b AS (SELECT id FROM a)
        SELECT id FROM b
        """
        result = extract_source_tables(sql)
        assert "lpp.orders" in result
        assert "a" not in result
        assert "b" not in result

    def test_subquery(self):
        sql = "SELECT * FROM lpp.orders WHERE id IN (SELECT order_id FROM lpp.items)"
        result = extract_source_tables(sql)
        assert "lpp.orders" in result
        assert "lpp.items" in result

    def test_none_input(self):
        assert extract_source_tables(None) == []

    def test_empty_string(self):
        assert extract_source_tables("") == []

    def test_invalid_sql(self):
        result = extract_source_tables("NOT VALID SQL AT ALL )()(")
        assert isinstance(result, list)

    def test_deduplication(self):
        sql = """
        SELECT a.id FROM lpp.orders a
        JOIN lpp.orders b ON a.id = b.id
        """
        result = extract_source_tables(sql)
        assert result.count("lpp.orders") == 1

    def test_union_query(self):
        sql = "SELECT id FROM lpp.orders UNION ALL SELECT id FROM lpp.items"
        result = extract_source_tables(sql)
        assert "lpp.orders" in result
        assert "lpp.items" in result

    def test_three_part_name(self):
        sql = "SELECT id FROM catalog.lpp.orders"
        result = extract_source_tables(sql)
        assert any("orders" in t for t in result)

"""Unit tests for nodes/schema_enricher — _select_anchor_columns bucketing."""

import pytest

from app.services.agents.nodes.schema_enricher import (
    _select_anchor_columns,
    _select_anchor_cols_for_table,
)


def _col(table_fqn="lpp.orders", name="id", semantic_type="identifier", data_type="varchar",
          referenced_table_fqn=None, value_vocabulary=None, distinct_values=None, **kwargs):
    """Factory for column dicts."""
    d = {
        "table_fqn": table_fqn,
        "name": name,
        "semantic_type": semantic_type,
        "data_type": data_type,
    }
    if referenced_table_fqn:
        d["referenced_table_fqn"] = referenced_table_fqn
    if value_vocabulary:
        d["value_vocabulary"] = value_vocabulary
    if distinct_values:
        d["distinct_values"] = distinct_values
    d.update(kwargs)
    return d


class TestSelectAnchorColsForTable:
    def test_priority_join_critical(self):
        cols = [
            _col(name="id", semantic_type="identifier"),
            _col(name="amount", semantic_type="amount"),
        ]
        result = _select_anchor_cols_for_table(cols, {("lpp.orders", "id")}, max_n=25)
        names = [c["name"] for c in result]
        assert names[0] == "id"

    def test_priority_foreign_key(self):
        cols = [
            _col(name="user_id", semantic_type="identifier", referenced_table_fqn="lpp.users"),
            _col(name="amount", semantic_type="amount"),
        ]
        result = _select_anchor_cols_for_table(cols, set(), max_n=25)
        names = [c["name"] for c in result]
        assert "user_id" in names[:2]

    def test_temporal_bucket(self):
        cols = [
            _col(name="created_at", semantic_type="date", data_type="timestamp"),
            _col(name="amount", semantic_type="amount"),
        ]
        result = _select_anchor_cols_for_table(cols, set(), max_n=25)
        names = [c["name"] for c in result]
        assert "created_at" in names

    def test_max_n_cap(self):
        cols = [_col(name=f"col_{i}", semantic_type="dimension") for i in range(50)]
        result = _select_anchor_cols_for_table(cols, set(), max_n=10)
        assert len(result) <= 10

    def test_semantic_type_ordering(self):
        cols = [
            _col(name="text_col", semantic_type="free_text"),
            _col(name="amount_col", semantic_type="amount"),
            _col(name="dim_col", semantic_type="dimension"),
        ]
        result = _select_anchor_cols_for_table(cols, set(), max_n=25)
        names = [c["name"] for c in result]
        assert names.index("amount_col") < names.index("text_col")


class TestSelectAnchorColumns:
    def test_groups_by_table(self):
        cols = [
            _col(table_fqn="lpp.orders", name="id"),
            _col(table_fqn="lpp.orders", name="total", semantic_type="amount"),
            _col(table_fqn="lpp.items", name="id"),
            _col(table_fqn="lpp.items", name="price", semantic_type="amount"),
        ]
        result = _select_anchor_columns(cols, set(), max_n=25)
        assert len(result) == 4

    def test_empty_input(self):
        assert _select_anchor_columns([], set(), max_n=25) == []

    def test_respects_per_table_cap(self):
        cols = [_col(table_fqn="lpp.big", name=f"c{i}", semantic_type="dimension") for i in range(100)]
        result = _select_anchor_columns(cols, set(), max_n=5)
        assert len(result) <= 5

"""Unit tests for semantic_ir — Pydantic model validators + coercion."""

import pytest

from app.services.agents.semantic_ir import (
    ColumnRef,
    FilterSpec,
    SemanticIR,
)


class TestColumnRefCoercion:
    def test_str_fields_from_list(self):
        col = ColumnRef(table_fqn=["lpp.orders"], column_name=["revenue"], alias=["rev"])
        assert col.table_fqn == "lpp.orders"
        assert col.column_name == "revenue"
        assert col.alias == "rev"

    def test_str_fields_from_empty_list(self):
        col = ColumnRef(table_fqn=[], column_name=[], alias=[])
        assert col.table_fqn == ""
        assert col.column_name == ""
        assert col.alias == ""

    def test_str_fields_from_none(self):
        col = ColumnRef(table_fqn=None, column_name=None, alias=None)
        assert col.table_fqn == ""
        assert col.column_name == ""
        assert col.alias == ""

    def test_aggregation_uppercase(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation="sum")
        assert col.aggregation == "SUM"

    def test_aggregation_none_string(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation="NONE")
        assert col.aggregation is None

    def test_aggregation_null_string(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation="null")
        assert col.aggregation is None

    def test_aggregation_empty_string(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation="")
        assert col.aggregation is None

    def test_aggregation_from_list(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation=["avg"])
        assert col.aggregation == "AVG"

    def test_aggregation_none_value(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", aggregation=None)
        assert col.aggregation is None

    def test_semantic_type_default(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a")
        assert col.semantic_type == "dimension"

    def test_semantic_type_from_empty(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", semantic_type="")
        assert col.semantic_type == "dimension"

    def test_semantic_type_from_list(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", semantic_type=["measure"])
        assert col.semantic_type == "measure"

    def test_semantic_type_lowercased(self):
        col = ColumnRef(table_fqn="t", column_name="c", alias="a", semantic_type="AMOUNT")
        assert col.semantic_type == "amount"


class TestFilterSpecCoercion:
    def test_operator_uppercase(self):
        f = FilterSpec(table_fqn="t", column_name="c", operator="in")
        assert f.operator == "IN"

    def test_operator_default(self):
        f = FilterSpec(table_fqn="t", column_name="c", operator="")
        assert f.operator == "="

    def test_operator_from_list(self):
        f = FilterSpec(table_fqn="t", column_name="c", operator=["between"])
        assert f.operator == "BETWEEN"

    def test_value_none_to_empty(self):
        f = FilterSpec(table_fqn="t", column_name="c", value=None)
        assert f.value == ""

    def test_value_nested_list_flatten(self):
        f = FilterSpec(table_fqn="t", column_name="c", value=[["USD", "EUR"]])
        assert f.value == ["USD", "EUR"]

    def test_value_normal_list_unchanged(self):
        f = FilterSpec(table_fqn="t", column_name="c", value=["USD", "EUR"])
        assert f.value == ["USD", "EUR"]

    def test_raw_user_value_from_list(self):
        f = FilterSpec(table_fqn="t", column_name="c", raw_user_value=["US", "UK"])
        assert f.raw_user_value == "US, UK"

    def test_raw_user_value_from_none(self):
        f = FilterSpec(table_fqn="t", column_name="c", raw_user_value=None)
        assert f.raw_user_value == ""

    def test_bool_from_string_true(self):
        f = FilterSpec(table_fqn="t", column_name="c", resolved="true", is_raw_sql="1", is_having="yes")
        assert f.resolved is True
        assert f.is_raw_sql is True
        assert f.is_having is True

    def test_bool_from_string_false(self):
        f = FilterSpec(table_fqn="t", column_name="c", resolved="false", is_raw_sql="0", is_having="no")
        assert f.resolved is False
        assert f.is_raw_sql is False
        assert f.is_having is False

    def test_str_field_from_list(self):
        f = FilterSpec(table_fqn=["lpp.t"], column_name=["col"])
        assert f.table_fqn == "lpp.t"
        assert f.column_name == "col"


class TestSemanticIRCoercion:
    def test_str_list_from_bare_string(self):
        ir = SemanticIR(anchor_tables="lpp.orders")
        assert ir.anchor_tables == ["lpp.orders"]

    def test_str_list_from_none(self):
        ir = SemanticIR(anchor_tables=None)
        assert ir.anchor_tables == []

    def test_str_list_flattens_nested(self):
        ir = SemanticIR(anchor_tables=[["lpp.a", "lpp.b"], "lpp.c"])
        assert ir.anchor_tables == ["lpp.a", "lpp.b", "lpp.c"]

    def test_str_list_drops_empty(self):
        ir = SemanticIR(anchor_tables=["lpp.a", "", None, "lpp.b"])
        assert ir.anchor_tables == ["lpp.a", "lpp.b"]

    def test_temporal_grains_from_string(self):
        ir = SemanticIR(temporal_grains="month")
        assert ir.temporal_grains == ["month"]

    def test_temporal_grains_nested_list(self):
        ir = SemanticIR(temporal_grains=[["month"]])
        assert ir.temporal_grains == ["month"]

    def test_temporal_grains_none(self):
        ir = SemanticIR(temporal_grains=None)
        assert ir.temporal_grains == []

    def test_temporal_grain_property(self):
        ir = SemanticIR(temporal_grains=["week", "month"])
        assert ir.temporal_grain == "week"

    def test_temporal_grain_empty(self):
        ir = SemanticIR(temporal_grains=[])
        assert ir.temporal_grain is None

    def test_limit_from_string(self):
        ir = SemanticIR(limit="100")
        assert ir.limit == 100

    def test_limit_from_float_string(self):
        ir = SemanticIR(limit="10.0")
        assert ir.limit == 10

    def test_limit_invalid_returns_none(self):
        ir = SemanticIR(limit="abc")
        assert ir.limit is None

    def test_limit_none(self):
        ir = SemanticIR(limit=None)
        assert ir.limit is None

    def test_complexity_valid(self):
        ir = SemanticIR(complexity="complex")
        assert ir.complexity == "complex"

    def test_complexity_invalid_defaults(self):
        ir = SemanticIR(complexity="impossible")
        assert ir.complexity == "simple"

    def test_complexity_empty(self):
        ir = SemanticIR(complexity="")
        assert ir.complexity == "simple"

    def test_result_shape_valid(self):
        ir = SemanticIR(result_shape="time_series")
        assert ir.result_shape == "time_series"

    def test_result_shape_invalid_defaults_table(self):
        ir = SemanticIR(result_shape="unknown")
        assert ir.result_shape == "table"

    def test_result_shape_none(self):
        ir = SemanticIR(result_shape=None)
        assert ir.result_shape is None

    def test_full_construction(self):
        ir = SemanticIR(
            template_id="tpl-1",
            intent="get revenue",
            complexity="simple",
            result_shape="kpi",
            anchor_tables=["lpp.orders"],
            measures=[ColumnRef(table_fqn="lpp.orders", column_name="total", alias="total", aggregation="sum")],
            filters=[FilterSpec(table_fqn="lpp.orders", column_name="status", operator="=", value="active")],
            temporal_grains=["month"],
            limit=50,
        )
        assert ir.template_id == "tpl-1"
        assert len(ir.measures) == 1
        assert ir.measures[0].aggregation == "SUM"
        assert ir.filters[0].operator == "="
        assert ir.limit == 50

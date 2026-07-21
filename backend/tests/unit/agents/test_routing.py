"""Unit tests for routing — conditional edge functions."""

import pytest
from unittest.mock import patch

from app.services.agents.routing import (
    route_intake,
    route_after_context_fetcher,
    route_after_anchor_resolver,
    route_after_intent_assembler,
    route_intent,
    route_compiler,
    route_filter_resolver,
    route_validator,
    route_executor,
    route_synthesis,
    route_should_compress,
    MAX_RECOMPILE,
    MAX_REPAIR,
)
from app.services.agents.node_names import (
    ANCHOR_RESOLVER,
    CHART_AGENT,
    COMPRESS,
    CONTEXT_FETCHER,
    DATA_QUALITY_CHECKER,
    DEEP_SENSITIVITY,
    DIRECTIVE_WRITER,
    ERROR_RESPONSE,
    EXECUTOR,
    FILTER_RESOLVER,
    GENERAL_CHAT,
    INTENT_RESOLVER,
    QUERY_COMPILER,
    QUERY_PLANNER,
    SQL_GENERATOR,
    SQL_VALIDATOR,
)


def _state(**overrides):
    base = {"thread_id": "test-thread"}
    base.update(overrides)
    return base


class TestRouteIntake:
    def test_error_routes_to_error_response(self):
        assert route_intake(_state(error="llm_unavailable")) == ERROR_RESPONSE

    def test_general_chat(self):
        assert route_intake(_state(question_type="general_chat")) == GENERAL_CHAT

    def test_analytics_default(self):
        assert route_intake(_state(question_type="analytics")) == CONTEXT_FETCHER

    def test_missing_question_type_defaults_analytics(self):
        assert route_intake(_state()) == CONTEXT_FETCHER


class TestRouteAfterContextFetcher:
    def test_semantic_layer_unavailable(self):
        result = route_after_context_fetcher(_state(error="semantic_layer_unavailable"))
        assert result == ERROR_RESPONSE

    def test_success(self):
        result = route_after_context_fetcher(_state())
        assert result == ANCHOR_RESOLVER


class TestRouteAfterAnchorResolver:
    def test_no_tables_fallback(self):
        result = route_after_anchor_resolver(_state(anchor_tables_resolved=[]))
        assert result == INTENT_RESOLVER

    def test_none_tables_fallback(self):
        result = route_after_anchor_resolver(_state(anchor_tables_resolved=None))
        assert result == INTENT_RESOLVER

    def test_tables_resolved(self):
        result = route_after_anchor_resolver(_state(anchor_tables_resolved=["lpp.orders"]))
        assert result == QUERY_PLANNER


class TestRouteAfterIntentAssembler:
    def test_fallback_flag(self):
        result = route_after_intent_assembler(_state(_intent_assembler_fallback=True))
        assert result == INTENT_RESOLVER

    def test_empty_resolved_intent(self):
        result = route_after_intent_assembler(_state(resolved_intent={}))
        assert result == INTENT_RESOLVER

    def test_success(self):
        result = route_after_intent_assembler(
            _state(resolved_intent={"anchor_tables": ["lpp.t"], "measures": [{"col": "x"}]})
        )
        assert result == DIRECTIVE_WRITER


class TestRouteIntent:
    def test_always_query_compiler(self):
        assert route_intent(_state()) == QUERY_COMPILER


class TestRouteCompiler:
    def test_error_no_ir(self):
        result = route_compiler(_state(error="compile_failed", semantic_ir_list=None))
        assert result == ERROR_RESPONSE

    def test_error_no_ir_empty_list(self):
        result = route_compiler(_state(error="compile_failed", semantic_ir_list=[]))
        assert result == ERROR_RESPONSE

    def test_success(self):
        result = route_compiler(_state(semantic_ir_list=[{"some": "ir"}]))
        assert result == FILTER_RESOLVER

    def test_no_error(self):
        result = route_compiler(_state())
        assert result == FILTER_RESOLVER


class TestRouteFilterResolver:
    def test_always_sql_generator(self):
        assert route_filter_resolver(_state()) == SQL_GENERATOR


class TestRouteValidator:
    def test_success_to_executor(self):
        assert route_validator(_state()) == EXECUTOR

    def test_error_in_repair_loop(self):
        result = route_validator(_state(error="invalid_sql", repair_count=1))
        assert result == ERROR_RESPONSE

    def test_error_recompile_available(self):
        result = route_validator(_state(error="invalid_sql", repair_count=0, recompile_count=0))
        assert result == SQL_GENERATOR

    def test_error_max_recompiles_reached(self):
        result = route_validator(_state(error="invalid_sql", repair_count=0, recompile_count=MAX_RECOMPILE))
        assert result == ERROR_RESPONSE


class TestRouteExecutor:
    @patch("app.services.agents.routing.settings")
    def test_success_dq_enabled(self, mock_settings):
        mock_settings.DATA_QUALITY_CHECKER_ENABLED = True
        result = route_executor(_state(repair_count=0, _prev_repair_count=0))
        assert result == DATA_QUALITY_CHECKER

    @patch("app.services.agents.routing.settings")
    def test_success_dq_disabled(self, mock_settings):
        mock_settings.DATA_QUALITY_CHECKER_ENABLED = False
        result = route_executor(_state(repair_count=0, _prev_repair_count=0))
        assert result == DEEP_SENSITIVITY

    @patch("app.services.agents.routing.settings")
    def test_stopped(self, mock_settings):
        mock_settings.DATA_QUALITY_CHECKER_ENABLED = False
        result = route_executor(_state(stopped=True, repair_count=0, _prev_repair_count=0))
        assert result == DEEP_SENSITIVITY

    @patch("app.services.agents.routing.settings")
    def test_error_repairs_exhausted(self, mock_settings):
        mock_settings.DATA_QUALITY_CHECKER_ENABLED = False
        result = route_executor(_state(error="exec_err", repair_count=MAX_REPAIR, _prev_repair_count=MAX_REPAIR))
        assert result == DEEP_SENSITIVITY

    @patch("app.services.agents.routing.settings")
    def test_repair_triggered(self, mock_settings):
        mock_settings.DATA_QUALITY_CHECKER_ENABLED = False
        result = route_executor(_state(repair_count=1, _prev_repair_count=0))
        assert result == SQL_VALIDATOR


class TestRouteSynthesis:
    def test_has_rows_to_chart(self):
        result = route_synthesis(_state(result_list=[{"rows": [{"a": 1}]}]))
        assert result == CHART_AGENT

    def test_no_data_skips_chart(self):
        result = route_synthesis(_state(result_list=[{"rows": []}], no_data=True))
        assert result != CHART_AGENT

    def test_empty_result_list(self):
        result = route_synthesis(_state(result_list=[]))
        assert result != CHART_AGENT


class TestRouteShouldCompress:
    def test_below_threshold(self):
        from langgraph.graph import END
        result = route_should_compress(_state(messages=[]))
        assert result == END

    def test_above_threshold(self):
        from app.services.agents.nodes.compress import SUMMARIZE_THRESHOLD
        msgs = [{"role": "user", "content": "hi"}] * SUMMARIZE_THRESHOLD
        result = route_should_compress(_state(messages=msgs))
        assert result == COMPRESS

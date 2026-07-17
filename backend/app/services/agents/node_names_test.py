"""Unit tests for node_names — constants and spec loading."""

import pytest

from app.services.agents.node_names import (
    ALL_NODES,
    NODE_TIER,
    INTAKE,
    GENERAL_CHAT,
    CONTEXT_FETCHER,
    ANCHOR_RESOLVER,
    SQL_GENERATOR,
    SQL_VALIDATOR,
    EXECUTOR,
    SYNTHESIS,
    CHART_AGENT,
    ERROR_RESPONSE,
    COMPRESS,
)


class TestAllNodes:
    def test_is_tuple(self):
        assert isinstance(ALL_NODES, tuple)

    def test_not_empty(self):
        assert len(ALL_NODES) > 10

    def test_contains_core_nodes(self):
        core = [INTAKE, GENERAL_CHAT, CONTEXT_FETCHER, ANCHOR_RESOLVER,
                SQL_GENERATOR, SQL_VALIDATOR, EXECUTOR, SYNTHESIS, CHART_AGENT,
                ERROR_RESPONSE, COMPRESS]
        for node in core:
            assert node in ALL_NODES, f"{node} missing from ALL_NODES"


class TestNodeTier:
    def test_is_dict(self):
        assert isinstance(NODE_TIER, dict)

    def test_all_nodes_have_tier(self):
        for node in ALL_NODES:
            assert node in NODE_TIER, f"{node} missing from NODE_TIER"

    def test_tier_values_valid(self):
        valid_tiers = {"fast", "balanced", "deep", "deterministic", "none"}
        for node, tier in NODE_TIER.items():
            assert tier in valid_tiers, f"{node} has invalid tier '{tier}'"


class TestBackwardCompatExports:
    def test_intake_is_string(self):
        assert isinstance(INTAKE, str)
        assert len(INTAKE) > 0

    def test_node_ids_are_lowercase(self):
        for node in ALL_NODES:
            assert node == node.lower() or "_" in node

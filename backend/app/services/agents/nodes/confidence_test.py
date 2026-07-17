"""Unit tests for nodes/confidence — _label scoring."""

import pytest

from app.services.agents.nodes.confidence import _label


class TestConfidenceLabel:
    def test_high(self):
        assert _label(75) == "High"
        assert _label(100) == "High"
        assert _label(95) == "High"

    def test_medium(self):
        assert _label(55) == "Medium"
        assert _label(74) == "Medium"
        assert _label(60) == "Medium"

    def test_low(self):
        assert _label(35) == "Low"
        assert _label(54) == "Low"
        assert _label(40) == "Low"

    def test_very_low(self):
        assert _label(34) == "Very Low"
        assert _label(0) == "Very Low"
        assert _label(10) == "Very Low"

    def test_boundary_75(self):
        assert _label(75) == "High"
        assert _label(74) == "Medium"

    def test_boundary_55(self):
        assert _label(55) == "Medium"
        assert _label(54) == "Low"

    def test_boundary_35(self):
        assert _label(35) == "Low"
        assert _label(34) == "Very Low"

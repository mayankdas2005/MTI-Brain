"""Unit tests for state — _specialist_outputs_reducer."""

import pytest

from app.services.agents.state import _specialist_outputs_reducer


class TestSpecialistOutputsReducer:
    def test_normal_append(self):
        existing = [{"node": "measure", "data": "m1"}]
        update = [{"node": "filter", "data": "f1"}]
        result = _specialist_outputs_reducer(existing, update)
        assert result == [
            {"node": "measure", "data": "m1"},
            {"node": "filter", "data": "f1"},
        ]

    def test_accumulates_multiple(self):
        existing = []
        update1 = [{"node": "measure"}]
        result1 = _specialist_outputs_reducer(existing, update1)
        update2 = [{"node": "filter"}]
        result2 = _specialist_outputs_reducer(result1, update2)
        update3 = [{"node": "dimension"}]
        result3 = _specialist_outputs_reducer(result2, update3)
        assert len(result3) == 3

    def test_reset_clears_list(self):
        existing = [{"node": "measure"}, {"node": "filter"}]
        update = [{"__reset__": True}, {"node": "new_measure"}]
        result = _specialist_outputs_reducer(existing, update)
        assert result == [{"node": "new_measure"}]

    def test_reset_with_no_following(self):
        existing = [{"node": "old"}]
        update = [{"__reset__": True}]
        result = _specialist_outputs_reducer(existing, update)
        assert result == []

    def test_empty_existing(self):
        result = _specialist_outputs_reducer([], [{"node": "x"}])
        assert result == [{"node": "x"}]

    def test_none_existing(self):
        result = _specialist_outputs_reducer(None, [{"node": "x"}])
        assert result == [{"node": "x"}]

    def test_empty_update(self):
        existing = [{"node": "a"}]
        result = _specialist_outputs_reducer(existing, [])
        assert result == [{"node": "a"}]

    def test_none_update(self):
        existing = [{"node": "a"}]
        result = _specialist_outputs_reducer(existing, None)
        assert result == [{"node": "a"}]

    def test_reset_flag_not_in_first_position(self):
        existing = [{"node": "old"}]
        update = [{"node": "new"}, {"__reset__": True}]
        result = _specialist_outputs_reducer(existing, update)
        assert result == [{"node": "old"}, {"node": "new"}, {"__reset__": True}]

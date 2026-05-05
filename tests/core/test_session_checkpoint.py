"""T1: SessionCheckpoint schema validation."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_checkpoint import SessionCheckpoint, compute_remaining

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestSessionCheckpoint:
    def test_round_trip_json(self) -> None:
        cp = SessionCheckpoint(
            completed_items=["item_a", "item_b"],
            step_name="step_1",
            progress_pct=50.0,
            ts="2026-05-04T12:00:00+00:00",
        )
        d = cp.to_dict()
        restored = SessionCheckpoint.from_dict(d)
        assert restored == cp

    def test_defaults(self) -> None:
        cp = SessionCheckpoint()
        assert cp.completed_items == []
        assert cp.step_name == ""
        assert cp.progress_pct == 0.0
        assert cp.ts == ""

    def test_from_dict_missing_fields_uses_defaults(self) -> None:
        cp = SessionCheckpoint.from_dict({})
        assert cp.completed_items == []
        assert cp.step_name == ""

    def test_now_factory(self) -> None:
        cp = SessionCheckpoint.now(["a", "b"], step_name="s1", progress_pct=75.0)
        assert cp.completed_items == ["a", "b"]
        assert cp.step_name == "s1"
        assert cp.progress_pct == 75.0
        assert cp.ts != ""

    def test_frozen(self) -> None:
        cp = SessionCheckpoint(completed_items=["x"])
        with pytest.raises(AttributeError):
            cp.step_name = "mutated"  # type: ignore[misc]


class TestComputeRemaining:
    def test_subtracts_completed(self) -> None:
        cp = SessionCheckpoint(completed_items=["a", "c"])
        remaining = compute_remaining(["a", "b", "c", "d"], cp)
        assert remaining == ["b", "d"]

    def test_preserves_order(self) -> None:
        cp = SessionCheckpoint(completed_items=["z"])
        remaining = compute_remaining(["z", "y", "x"], cp)
        assert remaining == ["y", "x"]

    def test_empty_checkpoint(self) -> None:
        cp = SessionCheckpoint()
        remaining = compute_remaining(["a", "b"], cp)
        assert remaining == ["a", "b"]

    def test_all_completed(self) -> None:
        cp = SessionCheckpoint(completed_items=["a", "b"])
        remaining = compute_remaining(["a", "b"], cp)
        assert remaining == []

"""Tests for fleet/_reset.py — dispatch artifact discovery and label computation."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import IssueLabelState
from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    find_dispatch_in_campaigns,
)
from autoskillit.fleet._reset import compute_reset_labels
from autoskillit.fleet.state import write_initial_state

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_state(tmp_path: Path, dispatch_name: str, **kwargs: object) -> Path:
    sp = tmp_path / "campaign.json"
    record = DispatchRecord(name=dispatch_name, **kwargs)  # type: ignore[arg-type]
    write_initial_state(sp, "cid-1", "test-campaign", "/m.yaml", [record])
    return sp


class TestFindDispatchInCampaigns:
    def test_match_by_dispatch_id(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "impl-issue-42", dispatch_id="d-abc123")
        result = find_dispatch_in_campaigns("d-abc123", [sp])
        assert result is not None
        dispatch, state_path = result
        assert dispatch.name == "impl-issue-42"
        assert state_path == sp

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "impl-issue-1", dispatch_id="d-1")
        assert find_dispatch_in_campaigns("nonexistent", [sp]) is None

    def test_match_by_name_fallback_for_refused_dispatch(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "task-x", dispatch_id="", status=DispatchStatus.REFUSED)
        result = find_dispatch_in_campaigns("task-x", [sp])
        assert result is not None
        dispatch, _state_path = result
        assert dispatch.name == "task-x"

    def test_multiple_campaigns_searched(self, tmp_path: Path) -> None:
        sp1 = _make_state(tmp_path / "a", "dispatch-1", dispatch_id="d-1")
        sp2 = _make_state(tmp_path / "b", "dispatch-2", dispatch_id="d-2")
        result = find_dispatch_in_campaigns("d-2", [sp1, sp2])
        assert result is not None
        dispatch, state_path = result
        assert dispatch.name == "dispatch-2"
        assert state_path == sp2

    def test_empty_state_file_skipped(self, tmp_path: Path) -> None:
        sp = tmp_path / "empty.json"
        sp.write_text("{}")
        assert find_dispatch_in_campaigns("anything", [sp]) is None

    def test_missing_state_file_skipped(self, tmp_path: Path) -> None:
        sp = tmp_path / "nonexistent.json"
        assert find_dispatch_in_campaigns("anything", [sp]) is None


class TestComputeResetLabels:
    def test_queued_target(self) -> None:
        remove, add = compute_reset_labels(IssueLabelState.QUEUED)
        assert remove == ["fail", "in-progress"]
        assert add == ["queued"]

    def test_fail_target(self) -> None:
        remove, add = compute_reset_labels(IssueLabelState.FAIL)
        assert remove == ["in-progress", "queued"]
        assert add == ["fail"]

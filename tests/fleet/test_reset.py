"""Tests for fleet/_reset.py — dispatch artifact discovery and label computation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core import IssueLabelState
from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    compute_reset_labels,
    find_dispatch_in_campaigns,
)
from autoskillit.fleet._reset import ResetReport, reset_dispatch_artifacts, update_campaign_state
from autoskillit.fleet.state import write_initial_state

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from autoskillit.core import SubprocessResult, TerminationReason

    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


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


class TestResetReport:
    def test_labels_reset_default_is_none(self) -> None:
        report = ResetReport()
        assert report.labels_reset is None


class TestResetDispatchArtifacts:
    @pytest.mark.anyio
    async def test_missing_sidecar_reports_labels_not_reset(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "gone.jsonl"
        dispatch = DispatchRecord(
            name="d-miss", sidecar_path=str(sidecar), status=DispatchStatus.FAILURE
        )
        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(return_value={"success": True})
        runner = AsyncMock(return_value=_make_subprocess_result())
        report = await reset_dispatch_artifacts(
            dispatch,
            project_dir=tmp_path,
            worktrees_dir=tmp_path / "wt",
            runner=runner,
            github_client=github_client,
            target_state=IssueLabelState.FAIL,
        )
        assert report.labels_reset is False
        assert any("MISSING" in e or "missing" in e for e in report.errors)
        github_client.swap_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_error_sidecar_reports_labels_not_reset(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "bad.jsonl"
        sidecar.mkdir()  # directory triggers IsADirectoryError (OSError subclass) → ERROR
        dispatch = DispatchRecord(
            name="d-err", sidecar_path=str(sidecar), status=DispatchStatus.FAILURE
        )
        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(return_value={"success": True})
        runner = AsyncMock(return_value=_make_subprocess_result())
        report = await reset_dispatch_artifacts(
            dispatch,
            project_dir=tmp_path,
            worktrees_dir=tmp_path / "wt",
            runner=runner,
            github_client=github_client,
            target_state=IssueLabelState.FAIL,
        )
        assert report.labels_reset is False
        assert len(report.errors) > 0
        github_client.swap_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_sidecar_read_exception_reports_labels_not_reset(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "explode.jsonl"
        dispatch = DispatchRecord(
            name="d-exc", sidecar_path=str(sidecar), status=DispatchStatus.FAILURE
        )
        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(return_value={"success": True})
        runner = AsyncMock(return_value=_make_subprocess_result())
        with patch(
            "autoskillit.fleet._reset.read_sidecar_from_path",
            side_effect=OSError("disk on fire"),
        ):
            report = await reset_dispatch_artifacts(
                dispatch,
                project_dir=tmp_path,
                worktrees_dir=tmp_path / "wt",
                runner=runner,
                github_client=github_client,
                target_state=IssueLabelState.FAIL,
            )
        assert report.labels_reset is False
        assert any("disk on fire" in e for e in report.errors)


class TestUpdateCampaignState:
    @pytest.mark.anyio
    async def test_fail_respects_labels_reset_false(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "d-test", status=DispatchStatus.FAILURE)
        result = await update_campaign_state(
            "d-test", sp, reset_to_queued=False, labels_reset=False
        )
        assert result is True
        from autoskillit.fleet.state import read_state

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "d-test")
        assert d.labels_cleaned is False

    @pytest.mark.anyio
    async def test_fail_sets_labels_cleaned_when_true(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "d-test2", status=DispatchStatus.FAILURE)
        result = await update_campaign_state(
            "d-test2", sp, reset_to_queued=False, labels_reset=True
        )
        assert result is True
        from autoskillit.fleet.state import read_state

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "d-test2")
        assert d.labels_cleaned is True

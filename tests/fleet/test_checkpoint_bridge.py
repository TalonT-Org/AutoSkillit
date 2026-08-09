"""Tests for checkpoint_from_sidecar and checkpoint_from_tracker bridge functions."""

from __future__ import annotations

import pytest

from autoskillit.core import ArtifactLease, TrackerAuthorityTarget, tracker_lease_path
from autoskillit.fleet._checkpoint_bridge import (
    checkpoint_from_sidecar,
    checkpoint_from_tracker,
    load_dispatch_progress,
)
from autoskillit.fleet.sidecar import (
    IssueSidecarEntry,
    append_sidecar_entry,
    sidecar_path,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

_PROVENANCE = {"backend_name": "codex", "skill_name": "implementation"}


class TestCheckpointFromSidecar:
    def test_extracts_completed_urls(self) -> None:
        entries = [
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/1", status="completed", ts="t1"
            ),
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/2", status="failed", ts="t2"
            ),
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/3", status="completed", ts="t3"
            ),
        ]
        cp = checkpoint_from_sidecar(entries, **_PROVENANCE)
        assert cp.completed_items == [
            "https://github.com/o/r/issues/1",
            "https://github.com/o/r/issues/3",
        ]
        assert cp.step_name == "fleet_dispatch"
        assert cp.backend_name == "codex"
        assert cp.skill_name == "implementation"

    def test_empty_entries(self) -> None:
        cp = checkpoint_from_sidecar([], **_PROVENANCE)
        assert cp.completed_items == []
        assert cp.ts == ""

    def test_uses_last_entry_ts(self) -> None:
        entries = [
            IssueSidecarEntry(issue_url="u1", status="completed", ts="2026-01-01"),
            IssueSidecarEntry(issue_url="u2", status="completed", ts="2026-05-04"),
        ]
        cp = checkpoint_from_sidecar(entries, **_PROVENANCE)
        assert cp.ts == "2026-05-04"

    def test_produces_valid_checkpoint(self) -> None:
        entries = [
            IssueSidecarEntry(issue_url="u1", status="completed", ts="t"),
        ]
        cp = checkpoint_from_sidecar(entries, **_PROVENANCE)
        d = cp.to_dict()
        from autoskillit.core.types._type_checkpoint import SessionCheckpoint

        restored = SessionCheckpoint.from_dict(d)
        assert restored == cp


class TestCheckpointFromTracker:
    def test_tracker_with_completed_steps_produces_checkpoint(self) -> None:
        tracker_data = {
            "pipeline_id": "dispatch-123",
            "steps": {
                "plan": {"status": "complete", "completed_at": "2026-06-01T00:00:00Z"},
                "verify": {"status": "complete", "completed_at": "2026-06-01T00:01:00Z"},
                "implement": {"status": "complete", "completed_at": "2026-06-01T00:02:00Z"},
                "review-pr": {"status": "pending"},
            },
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is not None
        assert checkpoint.completed_items == [
            "plan",
            "verify",
            "implement",
        ]  # sorted by completed_at
        assert checkpoint.step_name == "implement"
        assert checkpoint.progress_pct == pytest.approx(0.75)
        assert checkpoint.backend_name == "codex"
        assert checkpoint.skill_name == "implementation"

    def test_tracker_with_no_completed_steps_returns_none(self) -> None:
        tracker_data = {
            "pipeline_id": "dispatch-123",
            "steps": {
                "plan": {"status": "pending"},
                "verify": {"status": "pending"},
            },
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is None

    def test_tracker_empty_steps_returns_none(self) -> None:
        tracker_data = {
            "pipeline_id": "dispatch-123",
            "steps": {},
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is None

    def test_tracker_missing_file_returns_none(self) -> None:
        checkpoint = checkpoint_from_tracker(None, **_PROVENANCE)
        assert checkpoint is None

    def test_tracker_sorts_by_completed_at(self) -> None:
        tracker_data = {
            "pipeline_id": "dispatch-456",
            "steps": {
                "implement": {"status": "complete", "completed_at": "2026-06-01T00:02:00Z"},
                "plan": {"status": "complete", "completed_at": "2026-06-01T00:00:00Z"},
            },
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is not None
        assert checkpoint.step_name == "implement"
        assert checkpoint.ts == "2026-06-01T00:02:00Z"

    def test_tracker_produces_valid_checkpoint(self) -> None:
        from autoskillit.core.types._type_checkpoint import SessionCheckpoint

        tracker_data = {
            "pipeline_id": "dispatch-789",
            "steps": {
                "plan": {"status": "complete", "completed_at": "2026-06-01T00:00:00Z"},
            },
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is not None
        d = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(d)
        assert restored == checkpoint

    def test_tracker_ignores_non_dict_step_entries(self) -> None:
        tracker_data = {
            "pipeline_id": "dispatch-corrupt",
            "steps": {
                "plan": {"status": "complete", "completed_at": "2026-06-01T00:00:00Z"},
                "verify": "bad_data",
            },
        }
        checkpoint = checkpoint_from_tracker(tracker_data, **_PROVENANCE)
        assert checkpoint is not None
        assert checkpoint.completed_items == ["plan"]
        assert checkpoint.progress_pct == pytest.approx(0.5)

    def test_checkpoint_from_tracker_with_server_marked_steps(self) -> None:
        tracker_data = {
            "steps": {
                "rectify": {"status": "complete", "completed_at": "2026-07-01T12:00:00+00:00"},
                "review_approach": {"status": "pending"},
            },
        }
        checkpoint = checkpoint_from_tracker(
            tracker_data, backend_name="claude-code", skill_name="test"
        )
        assert checkpoint is not None
        assert checkpoint.completed_items == ["rectify"]
        assert checkpoint.step_name == "rectify"
        assert checkpoint.progress_pct == pytest.approx(0.5)


class TestLoadDispatchProgress:
    def test_missing_expected_tracker_returns_fourth_authority_error(
        self, tool_ctx, tmp_path
    ) -> None:
        tool_ctx.project_dir = tmp_path
        dispatch_id = "dispatch-missing"
        target = TrackerAuthorityTarget.for_project(tmp_path, dispatch_id, expected=True)
        lease = ArtifactLease.acquire_shared(tracker_lease_path(target))
        try:
            result = load_dispatch_progress(
                tool_ctx=tool_ctx,
                dispatch_sidecar_path=str(sidecar_path(dispatch_id, tmp_path)),
                dispatch_id=dispatch_id,
                backend_name="codex",
                recipe="implementation",
                tracker_lease=lease,
            )
        finally:
            lease.close_preserving()

        assert len(result) == 4
        sidecar_file, entries, checkpoint, authority_error = result
        assert sidecar_file == sidecar_path(dispatch_id, tmp_path)
        assert entries == []
        assert checkpoint is None
        assert authority_error is not None
        assert dispatch_id in authority_error

    def test_authoritative_sidecar_suppresses_tracker_authority_error(
        self, tool_ctx, tmp_path
    ) -> None:
        tool_ctx.project_dir = tmp_path
        dispatch_id = "dispatch-sidecar"
        append_sidecar_entry(
            dispatch_id,
            IssueSidecarEntry(issue_url="issue-1", status="completed", ts="t1"),
            tmp_path,
        )
        target = TrackerAuthorityTarget.for_project(tmp_path, dispatch_id, expected=True)
        lease = ArtifactLease.acquire_shared(tracker_lease_path(target))
        try:
            _path, entries, checkpoint, authority_error = load_dispatch_progress(
                tool_ctx=tool_ctx,
                dispatch_sidecar_path=str(sidecar_path(dispatch_id, tmp_path)),
                dispatch_id=dispatch_id,
                backend_name="codex",
                recipe="implementation",
                tracker_lease=lease,
            )
        finally:
            lease.close_preserving()

        assert [entry.issue_url for entry in entries] == ["issue-1"]
        assert checkpoint is not None
        assert checkpoint.completed_items == ["issue-1"]
        assert authority_error is None

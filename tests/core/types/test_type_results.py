"""Tests for core result type contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import DelegatedWriteTarget, WriteEvidence

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestWriteEvidence:
    def test_none_observed_all_zero(self) -> None:
        we = WriteEvidence.none_observed()
        assert we.write_call_count == 0
        assert we.fs_writes_detected is False
        assert we.git_writes_detected is False
        assert we.file_changes_count == 0
        assert we.delegated_write_count == 0

    def test_has_evidence_true_from_write_call_count(self) -> None:
        we = WriteEvidence(write_call_count=3, fs_writes_detected=False, git_writes_detected=False)
        assert we.has_evidence is True

    def test_has_evidence_true_from_fs_writes(self) -> None:
        we = WriteEvidence(write_call_count=0, fs_writes_detected=True, git_writes_detected=False)
        assert we.has_evidence is True

    def test_has_evidence_true_from_git_writes(self) -> None:
        we = WriteEvidence(write_call_count=0, fs_writes_detected=False, git_writes_detected=True)
        assert we.has_evidence is True

    def test_has_evidence_false_when_none_observed(self) -> None:
        assert WriteEvidence.none_observed().has_evidence is False

    def test_frozen_rejects_assignment(self) -> None:
        we = WriteEvidence(write_call_count=1, fs_writes_detected=False, git_writes_detected=False)
        with pytest.raises(AttributeError):
            we.write_call_count = 5  # type: ignore[misc]

    def test_has_evidence_true_from_file_changes_count(self) -> None:
        we = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=1,
        )
        assert we.has_evidence is True

    def test_delegated_write_count_is_implementation_evidence(self) -> None:
        ev = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            delegated_write_count=1,
        )

        assert ev.has_evidence is True
        assert ev.has_implementation_evidence is True

    def test_none_observed_includes_file_changes_count_zero(self) -> None:
        we = WriteEvidence.none_observed()
        assert we.file_changes_count == 0
        assert we.has_evidence is False

    def test_file_changes_count_default_is_zero(self) -> None:
        we = WriteEvidence(write_call_count=0, fs_writes_detected=False, git_writes_detected=False)
        assert we.file_changes_count == 0

    def test_has_implementation_evidence_excludes_infra_only(self) -> None:
        ev = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=True,
            git_writes_detected=True,
            file_changes_count=0,
        )
        assert ev.has_implementation_evidence is False
        assert ev.has_evidence is True

    def test_has_implementation_evidence_true_with_write_calls(self) -> None:
        ev = WriteEvidence(
            write_call_count=1,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=0,
        )
        assert ev.has_implementation_evidence is True

    def test_has_implementation_evidence_true_with_file_changes(self) -> None:
        ev = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=1,
        )
        assert ev.has_implementation_evidence is True

    def test_delegated_write_paths_alone_are_not_evidence(self) -> None:
        defaults = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
        )
        paths_only = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            delegated_write_paths=("/workspace/plan.md",),
        )

        assert defaults.delegated_write_paths == ()
        assert paths_only.delegated_write_paths == ("/workspace/plan.md",)
        assert paths_only.has_implementation_evidence is False
        assert paths_only.has_evidence is False


class TestDelegatedWriteTarget:
    @pytest.mark.parametrize("operation", ["stat", "read_bytes"])
    def test_bind_propagates_unexpected_io_errors(
        self, tmp_path: Path, monkeypatch, operation: str
    ) -> None:
        path = tmp_path / "plan.md"
        path.write_bytes(b"plan")

        def unreadable(*args, **kwargs):
            raise PermissionError("target unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(Path, operation, unreadable)
            with pytest.raises(PermissionError, match="target unavailable"):
                DelegatedWriteTarget.bind(str(path))

    @pytest.mark.parametrize("is_directory", [False, True], ids=["missing", "directory"])
    def test_bind_records_no_baseline_for_non_files(
        self, tmp_path: Path, is_directory: bool
    ) -> None:
        path = tmp_path / "target"
        if is_directory:
            path.mkdir()

        target = DelegatedWriteTarget.bind(str(path.resolve()))

        assert target.baseline_digest is None

    def test_changed_tracks_content_and_ignores_identical_bytes_or_deletion(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "plan.md"
        path.write_bytes(b"before")
        target = DelegatedWriteTarget.bind(str(path.resolve()))

        path.write_bytes(b"before")
        assert target.changed() is False

        path.write_bytes(b"after")
        assert target.changed() is True

        path.unlink()
        assert target.changed() is False


class TestDelegatedOutputScope:
    def test_target_for_accepts_only_fresh_paths_inside_root(self, tmp_path: Path) -> None:
        from autoskillit.core import DelegatedOutputScope

        root = tmp_path / "outputs"
        root.mkdir()
        preexisting = root / "old-plan.md"
        preexisting.write_text("existing")
        fresh = root / "new-plan.md"
        outside = tmp_path / "outside.md"
        scope = DelegatedOutputScope(
            str(root.resolve()),
            frozenset({str(preexisting.resolve())}),
        )

        assert scope.target_for(str(outside.resolve())) is None
        assert scope.target_for(str(root.resolve())) is None
        assert scope.target_for(str(preexisting.resolve())) is None

        target = scope.target_for(str(fresh.resolve()))
        assert target == DelegatedWriteTarget(str(fresh.resolve()), None)
        assert target is not None
        assert target.changed() is False

        fresh.write_text("new plan")
        assert target.changed() is True


class TestValidateRolloutMarker:
    def test_normalizes_utc_timestamp_and_copies_scope(self) -> None:
        from autoskillit.core import validate_rollout_marker

        scope = {"skills": ["make-plan"], "backends": ["codex"], "roles": []}
        raw = {
            "change_id": "prompt-cost-study",
            "effective_at": "2026-11-01T18:00:00Z",
            "scope": scope,
            "expected_effect": "Reduce planning input tokens.",
        }

        marker = validate_rollout_marker(raw)
        scope["skills"].append("later-skill")

        assert marker["effective_at"] == "2026-11-01T18:00:00+00:00"
        assert marker["scope"] == {
            "skills": ["make-plan"],
            "backends": ["codex"],
            "roles": [],
        }

    @pytest.mark.parametrize(
        "effective_at",
        ["2026-11-01T18:00:00", "2026-11-01T18:00:00-07:00", "not-a-date", 1700000000],
        ids=["naive", "non-utc", "malformed", "non-string"],
    )
    def test_rejects_timestamps_outside_explicit_utc_contract(self, effective_at: object) -> None:
        from autoskillit.core import validate_rollout_marker

        raw = {
            "change_id": "prompt-cost-study",
            "effective_at": effective_at,
            "scope": {"skills": [], "backends": [], "roles": []},
            "expected_effect": "Reduce planning input tokens.",
        }

        with pytest.raises(ValueError, match="rollout.active.effective_at"):
            validate_rollout_marker(raw)


class TestHasImplementationProgress:
    def test_excludes_infrastructure_writes(self) -> None:
        from autoskillit.core.types import RetryReason, SkillResult

        ev = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=True,
            git_writes_detected=True,
            file_changes_count=0,
        )
        sr = SkillResult(
            success=True,
            result="done",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            worktree_path="/tmp/wt",
            evidence=ev,
        )
        assert sr.has_progress_evidence is True
        assert sr.has_implementation_progress is False


class TestSessionIndexExecutionIdentitySchema:
    def test_child_executions_uses_typed_persistence_shape(self) -> None:
        from typing import get_type_hints

        from autoskillit.core.types import (
            ChildExecutionIdentityDict,
            SessionIndexEntry,
            SubagentModelOutcomeDict,
        )

        hints = get_type_hints(SessionIndexEntry)
        assert hints["child_executions"] == list[ChildExecutionIdentityDict]
        assert hints["subagent_model_outcomes"] == list[SubagentModelOutcomeDict]

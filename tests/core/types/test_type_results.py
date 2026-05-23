"""Tests for WriteEvidence bundle type safety."""

from __future__ import annotations

import pytest

from autoskillit.core.types import WriteEvidence

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestWriteEvidence:
    def test_none_observed_all_zero(self) -> None:
        we = WriteEvidence.none_observed()
        assert we.write_call_count == 0
        assert we.fs_writes_detected is False
        assert we.git_writes_detected is False
        assert we.file_changes_count == 0

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

    def test_none_observed_includes_file_changes_count_zero(self) -> None:
        we = WriteEvidence.none_observed()
        assert we.file_changes_count == 0
        assert we.has_evidence is False

    def test_file_changes_count_default_is_zero(self) -> None:
        we = WriteEvidence(write_call_count=0, fs_writes_detected=False, git_writes_detected=False)
        assert we.file_changes_count == 0

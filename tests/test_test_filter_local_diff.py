"""Tests for git_changed_files_local() — working-tree-only diff for aggressive mode."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest

from tests._test_filter import git_changed_files_local

pytestmark = [pytest.mark.medium]


class TestGitChangedFilesLocal:
    def test_local_diff_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns union of staged+unstaged changes vs HEAD plus untracked files."""
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="src/autoskillit/core/io.py\n"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="new_file.py\n"),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files_local("/fake")
        assert result == {"src/autoskillit/core/io.py", "new_file.py"}
        assert mock_run.call_count == 2
        first_call = mock_run.call_args_list[0][0][0]
        assert first_call == ["git", "diff", "HEAD", "--name-only"]

    def test_local_diff_clean_worktree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clean working tree returns empty set (not None — diff succeeded)."""
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files_local("/fake")
        assert result == set()

    def test_local_diff_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Git failure returns None (fail-open)."""

        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files_local("/fake")
        assert result is None

    def test_local_diff_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Git timeout returns None (fail-open)."""

        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files_local("/fake")
        assert result is None

    def test_local_diff_ls_files_failure_nonfatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ls-files failure is non-fatal — returns diff results only."""
        mock_run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=0, stdout="changed.py\n"),
                subprocess.CompletedProcess(args=[], returncode=1, stdout=""),
            ]
        )
        monkeypatch.setattr(subprocess, "run", mock_run)
        result = git_changed_files_local("/fake")
        assert result == {"changed.py"}

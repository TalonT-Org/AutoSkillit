"""Tests for autoskillit.core.paths.resolve_main_worktree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def _git(*args: str) -> None:
    subprocess.run(["git", *args], capture_output=True, text=True, check=True, env=_GIT_ENV)


class TestResolveMainWorktree:
    def test_resolve_main_worktree_from_main_checkout(self, tmp_path: Path) -> None:
        """Main checkout resolves to itself."""
        from autoskillit.core.paths import resolve_main_worktree

        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", str(repo))
        result = resolve_main_worktree(repo)
        assert result is not None
        assert result == repo.resolve()

    def test_resolve_main_worktree_from_linked_worktree(self, tmp_path: Path) -> None:
        """Worktree path resolves to the main checkout root."""
        from autoskillit.core.paths import resolve_main_worktree

        main = tmp_path / "project"
        main.mkdir()
        _git("init", str(main))
        _git("-C", str(main), "commit", "--allow-empty", "-m", "init")

        wt_path = tmp_path / "worktrees" / "wt1"
        _git("-C", str(main), "worktree", "add", "-b", "wt1", str(wt_path))

        result = resolve_main_worktree(wt_path)
        assert result is not None
        assert result == main.resolve()

    def test_resolve_main_worktree_from_nested_worktree(self, tmp_path: Path) -> None:
        """Worktree created from another worktree still resolves to main root."""
        from autoskillit.core.paths import resolve_main_worktree

        main = tmp_path / "project"
        main.mkdir()
        _git("init", str(main))
        _git("-C", str(main), "commit", "--allow-empty", "-m", "init")

        wt1_path = tmp_path / "worktrees" / "wt1"
        _git("-C", str(main), "worktree", "add", "-b", "wt1", str(wt1_path))

        wt2_path = tmp_path / "worktrees" / "worktrees" / "wt2"
        _git("-C", str(wt1_path), "worktree", "add", "-b", "wt2", str(wt2_path))

        result = resolve_main_worktree(wt2_path)
        assert result is not None
        assert result == main.resolve()

    def test_resolve_main_worktree_from_non_git_dir_returns_none(self, tmp_path: Path) -> None:
        """Directory with no .git returns None."""
        from autoskillit.core.paths import resolve_main_worktree

        result = resolve_main_worktree(tmp_path)
        assert result is None

    def test_resolve_main_worktree_from_subdirectory_of_main(self, tmp_path: Path) -> None:
        """Subdirectory of main checkout resolves to main checkout root."""
        from autoskillit.core.paths import resolve_main_worktree

        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", str(repo))
        subdir = repo / "src" / "autoskillit"
        subdir.mkdir(parents=True)

        result = resolve_main_worktree(subdir)
        assert result is not None
        assert result == repo.resolve()

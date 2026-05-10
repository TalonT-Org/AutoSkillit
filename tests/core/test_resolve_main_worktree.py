"""Tests for autoskillit.core.paths.resolve_main_worktree."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestResolveMainWorktree:
    def test_resolve_main_worktree_from_main_checkout(self, tmp_path: Path) -> None:
        """Main checkout resolves to itself."""
        from autoskillit.core.paths import resolve_main_worktree

        (tmp_path / ".git").mkdir()
        result = resolve_main_worktree(tmp_path)
        assert result is not None
        assert result == tmp_path.resolve()

    def test_resolve_main_worktree_from_linked_worktree(self, tmp_path: Path) -> None:
        """Worktree path resolves to the main checkout root."""
        from autoskillit.core.paths import resolve_main_worktree

        main = tmp_path / "project"
        main.mkdir()
        (main / ".git").mkdir()
        worktrees_dir = main / ".git" / "worktrees"
        worktrees_dir.mkdir(parents=True)
        wt1 = worktrees_dir / "wt1"
        wt1.mkdir()
        (wt1 / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        # Actually create the worktree on disk
        real_wt = tmp_path / "worktrees" / "wt1"
        real_wt.mkdir(parents=True)
        (real_wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        result = resolve_main_worktree(real_wt)
        assert result is not None
        assert result == main.resolve()

    def test_resolve_main_worktree_from_nested_worktree(self, tmp_path: Path) -> None:
        """Deeply nested worktree resolves to the main checkout root."""
        from autoskillit.core.paths import resolve_main_worktree

        main = tmp_path / "project"
        main.mkdir()
        (main / ".git").mkdir()

        # Create first worktree
        wt1 = main / ".git" / "worktrees" / "wt1"
        wt1.mkdir(parents=True)
        real_wt1 = tmp_path / "worktrees" / "wt1"
        real_wt1.mkdir(parents=True)
        (real_wt1 / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        result = resolve_main_worktree(real_wt1)
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

        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "autoskillit"
        subdir.mkdir(parents=True)

        result = resolve_main_worktree(subdir)
        assert result is not None
        assert result == tmp_path.resolve()

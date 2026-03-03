"""Tests for autoskillit.core.paths — is_git_worktree and pkg_root."""

from __future__ import annotations

import importlib.resources as ir
from pathlib import Path


class TestWorktreeDetection:
    def test_detects_main_checkout_as_not_worktree(self, tmp_path: Path) -> None:
        """A directory with a .git DIRECTORY is the main checkout, not a worktree."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").mkdir()
        assert is_git_worktree(tmp_path) is False

    def test_detects_linked_worktree_via_git_file(self, tmp_path: Path) -> None:
        """A directory with a .git FILE is a linked worktree."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").write_text("gitdir: /path/to/main/.git/worktrees/foo\n")
        assert is_git_worktree(tmp_path) is True

    def test_detects_worktree_from_subdirectory(self, tmp_path: Path) -> None:
        """Detection works when called from a subdirectory of the worktree root."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").write_text("gitdir: /path/to/main/.git/worktrees/foo\n")
        subdir = tmp_path / "src" / "autoskillit"
        subdir.mkdir(parents=True)
        assert is_git_worktree(subdir) is True

    def test_not_in_git_repo_returns_false(self, tmp_path: Path) -> None:
        """Directories with no .git ancestor return False (not a worktree)."""
        from autoskillit.core.paths import is_git_worktree

        assert is_git_worktree(tmp_path) is False


class TestPkgRoot:
    def test_pkg_root_matches_importlib_resources(self) -> None:
        """pkg_root() must return the same path as importlib.resources.files('autoskillit')."""
        from autoskillit.core.paths import pkg_root

        assert pkg_root() == Path(ir.files("autoskillit"))

    def test_pkg_root_is_package_directory(self) -> None:
        """pkg_root() must return the autoskillit package root directory."""
        from autoskillit.core.paths import pkg_root

        result = pkg_root()
        assert (result / "__init__.py").is_file(), (
            "pkg_root() must return the autoskillit package root"
        )
        assert result.name == "autoskillit", (
            "pkg_root() must return the autoskillit package directory"
        )

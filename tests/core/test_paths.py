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


class TestClaudeCodeLogPath:
    def test_claude_code_log_path_with_real_session_id(self) -> None:
        from autoskillit.core.paths import claude_code_log_path

        result = claude_code_log_path(cwd="/home/user/my_project", session_id="abc-123")
        assert (
            result
            == Path.home() / ".claude" / "projects" / "-home-user-my-project" / "abc-123.jsonl"
        )

    def test_claude_code_log_path_with_fallback_session_id(self) -> None:
        from autoskillit.core.paths import claude_code_log_path

        result = claude_code_log_path(
            cwd="/home/user/project", session_id="no_session_2026-03-03T12-00-00+00-00"
        )
        assert result is None

    def test_claude_code_log_path_with_crashed_session_id(self) -> None:
        from autoskillit.core.paths import claude_code_log_path

        result = claude_code_log_path(
            cwd="/home/user/project", session_id="crashed_12345_2026-03-03T12-00-00+00-00"
        )
        assert result is None

    def test_claude_code_log_path_with_empty_session_id(self) -> None:
        from autoskillit.core.paths import claude_code_log_path

        result = claude_code_log_path(cwd="/home/user/project", session_id="")
        assert result is None

    def test_claude_code_project_dir_encoding(self) -> None:
        from autoskillit.core.paths import claude_code_project_dir

        result = claude_code_project_dir("/home/user/my_project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-my-project"


class TestFindLatestSessionId:
    # REQ-CLI-002
    def test_returns_most_recent(self, tmp_path: Path) -> None:
        import time
        from unittest.mock import patch

        from autoskillit.core.paths import find_latest_session_id

        proj_dir = tmp_path / "projects" / "-home-user-myproject"
        proj_dir.mkdir(parents=True)
        older = proj_dir / "aaa111.jsonl"
        older.write_text("{}")
        time.sleep(0.01)
        newer = proj_dir / "bbb222.jsonl"
        newer.write_text("{}")

        with patch("autoskillit.core.paths.claude_code_project_dir", return_value=proj_dir):
            result = find_latest_session_id("/home/user/myproject")
        assert result == "bbb222"

    def test_returns_none_empty_dir(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from autoskillit.core.paths import find_latest_session_id

        proj_dir = tmp_path / "empty"
        proj_dir.mkdir()
        with patch("autoskillit.core.paths.claude_code_project_dir", return_value=proj_dir):
            result = find_latest_session_id("/home/user/myproject")
        assert result is None

    def test_returns_none_no_dir(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from autoskillit.core.paths import find_latest_session_id

        missing = tmp_path / "nonexistent"
        with patch("autoskillit.core.paths.claude_code_project_dir", return_value=missing):
            result = find_latest_session_id("/home/user/myproject")
        assert result is None

    def test_uses_cwd_when_none(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from autoskillit.core.paths import find_latest_session_id

        proj_dir = tmp_path / "projects" / "cwd"
        proj_dir.mkdir(parents=True)
        (proj_dir / "ccc333.jsonl").write_text("{}")

        with (
            patch("autoskillit.core.paths.claude_code_project_dir", return_value=proj_dir),
            patch("pathlib.Path.cwd", return_value=Path("/fake/cwd")),
        ):
            result = find_latest_session_id()  # cwd=None → uses Path.cwd()
        assert result == "ccc333"


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

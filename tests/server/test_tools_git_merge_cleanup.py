"""Tests for merge_worktree cleanup reporting and warnings."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import structlog.testing

from autoskillit.core import CleanupResult
from autoskillit.server.tools.tools_git import merge_worktree
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestMergeWorktreeCleanupReporting:
    """merge_worktree reports accurate cleanup results."""

    @pytest.mark.anyio
    async def test_reports_worktree_remove_failure(self, tool_ctx_kitchen_open, tmp_path):
        """3a: worktree_removed reflects actual worktree removal result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # pre-rebase test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # post-rebase test-check
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "dev\n", "")
        )  # step 7.5: branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # step 7.6: git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # branch -D
        with (
            patch(
                "autoskillit.server.git.remove_git_worktree",
                new=AsyncMock(
                    return_value=CleanupResult(failed=[(str(wt), "error: untracked files")])
                ),
            ),
            patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")),
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is False

    @pytest.mark.anyio
    async def test_reports_branch_delete_failure(self, tool_ctx_kitchen_open, tmp_path):
        """3b: branch_deleted reflects actual git branch -D result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # pre-rebase test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # post-rebase test-check
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "dev\n", "")
        )  # step 7.5: branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # step 7.6: git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "", "error: branch not found")
        )  # branch -D FAILS
        with patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")):
            result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is False


class TestMergeWorktreeCleanupWarnings:
    """merge_worktree emits logger.warning when cleanup steps fail post-merge."""

    @pytest.mark.anyio
    async def test_warns_on_worktree_remove_failure(self, tool_ctx_kitchen_open, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # pre-rebase test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # post-rebase test-check
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "dev\n", "")
        )  # step 7.5: branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # step 7.6: git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # branch -D

        with (
            patch(
                "autoskillit.server.git.remove_git_worktree",
                new=AsyncMock(
                    return_value=CleanupResult(failed=[(str(wt), "error: untracked files")])
                ),
            ),
            patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is False
        warning_entries = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any(entry.get("operation") == "worktree_remove" for entry in warning_entries)

    @pytest.mark.anyio
    async def test_warns_on_branch_delete_failure(self, tool_ctx_kitchen_open, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # pre-rebase test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # post-rebase test-check
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "dev\n", "")
        )  # step 7.5: branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # step 7.6: git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "", "error: branch not found")
        )  # branch -D FAILS

        with (
            patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["branch_deleted"] is False
        warning_entries = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any(entry.get("operation") == "branch_delete" for entry in warning_entries)

    @pytest.mark.anyio
    async def test_no_warning_on_clean_cleanup(self, tool_ctx_kitchen_open, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # pre-rebase test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "PASS\n= 100 passed =", "")
        )  # post-rebase test-check
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "dev\n", "")
        )  # step 7.5: branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # step 7.6: git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # worktree remove — success
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # branch -D — success

        with (
            patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is True
        cleanup_warnings = [
            entry
            for entry in logs
            if entry.get("log_level") == "warning" and "cleanup" in str(entry.get("event", ""))
        ]
        assert cleanup_warnings == []

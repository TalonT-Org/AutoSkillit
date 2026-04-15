"""Tests for classify_fix and merge_worktree MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import structlog.testing

from autoskillit.config import AutomationConfig, ClassifyFixConfig
from autoskillit.core import CleanupResult
from autoskillit.core.types import MergeFailedStep, MergeState, RestartScope
from autoskillit.server.tools_git import (
    check_pr_mergeable,
    classify_fix,
    create_unique_branch,
    merge_worktree,
)
from tests.conftest import _make_result


class TestClassifyFix:
    """T4, T5: classify_fix returns correct restart scope based on changed files."""

    @pytest.fixture(autouse=True)
    def _set_prefixes(self, tool_ctx):
        """Configure critical path prefixes for classify_fix tests."""
        tool_ctx.config = AutomationConfig(
            classify_fix=ClassifyFixConfig(
                path_prefixes=[
                    "src/core/",
                    "src/api/",
                    "lib/handlers/",
                ]
            )
        )

    @pytest.mark.anyio
    async def test_critical_files_return_full_restart(self, tool_ctx, tmp_path):
        changed = "src/core/handler.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert len(result["critical_files"]) == 1
        assert result["critical_files"][0] == "src/core/handler.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_non_critical_returns_partial_restart(self, tool_ctx, tmp_path):
        changed = "src/workers/runner.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["critical_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_git_diff_failure(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(128, "", "fatal: bad revision"))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert "restart_scope" in result
        assert "Cannot diff" in result["reason"]

    @pytest.mark.anyio
    async def test_critical_path_in_diff_triggers_full_restart(self, tool_ctx, tmp_path):
        changed = "src/api/routes.py\n"
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART

    @pytest.mark.anyio
    async def test_classify_fix_nonexistent_worktree_path_returns_clear_error(self, tool_ctx):
        """[FAILS NOW] nonexistent path returns a distinct path-not-found error."""
        result = json.loads(await classify_fix("/no/such/path", "main"))
        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert "does not exist" in result["reason"].lower()

    @pytest.mark.anyio
    async def test_classify_fix_git_fetch_called_before_diff(self, tool_ctx, tmp_path):
        """[FAILS NOW] git fetch must be issued before git diff."""
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch succeeds
        tool_ctx.runner.push(_make_result(0, "src/foo.py\n", ""))  # diff succeeds
        await classify_fix(str(tmp_path), "main")
        assert tool_ctx.runner.call_args_list[0][0] == ["git", "fetch", "origin", "main"]
        assert tool_ctx.runner.call_args_list[1][0][0:3] == ["git", "diff", "--name-only"]

    @pytest.mark.anyio
    async def test_classify_fix_gate_closed_returns_gate_error(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """[NEW COVERAGE] gate closed path returns gate_error."""
        from autoskillit.pipeline import DefaultGateState

        monkeypatch.setattr(tool_ctx, "gate", DefaultGateState(enabled=False))
        result = json.loads(await classify_fix(str(tmp_path), "main"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_classify_fix_empty_diff_returns_partial_restart_with_no_files(
        self, tool_ctx, tmp_path
    ):
        """[NEW COVERAGE] empty diff is a valid state returning partial_restart with no files."""
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # diff (empty)
        result = json.loads(await classify_fix(str(tmp_path), "main"))
        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["all_changed_files"] == []
        assert result["critical_files"] == []


class TestMergeWorktree:
    """merge_worktree enforces test gate, rebases, and merges."""

    @pytest.mark.anyio
    async def test_merge_worktree_blocks_on_failing_tests(self, tool_ctx, tmp_path):
        """merge_worktree returns error with failed_step when test-check fails."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse --git-dir
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(1, "FAIL\n= 3 failed, 97 passed =", ""))  # test-check
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE
        assert result["state"] == MergeState.WORKTREE_INTACT
        assert "test_summary" not in result

    @pytest.mark.anyio
    async def test_merge_worktree_merges_on_green(self, tool_ctx, tmp_path):
        """merge_worktree performs rebase+merge when tests pass."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc123\nbranch refs/heads/dev\n\n"
                "worktree /wt\nHEAD def456\nbranch refs/heads/impl-branch\n\n",
                "",
            )
        )  # worktree list --porcelain
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["into_branch"] == "dev"
        assert result["cleanup_succeeded"] is True
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True
        # Verify merge command cwd is the main_repo (/repo)
        merge_call = next(
            args
            for args in tool_ctx.runner.call_args_list
            if len(args[0]) > 1 and args[0][1] == "merge"
        )
        assert merge_call[1] == Path("/repo")

    @pytest.mark.anyio
    async def test_merge_worktree_aborts_on_rebase_failure(self, tool_ctx, tmp_path):
        """merge_worktree runs rebase --abort and returns step-specific error."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(1, "", "CONFLICT (content): ..."))  # git rebase FAILS
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase --abort
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.REBASE
        assert result["state"] == MergeState.WORKTREE_INTACT_REBASE_ABORTED
        assert result.get("stderr"), (
            f"merge_worktree rebase failure must include non-empty stderr diagnostic. "
            f"Got result={result!r}"
        )
        assert "CONFLICT" in result["stderr"]

    @pytest.mark.anyio
    async def test_merge_worktree_rejects_nonexistent_path(self, tool_ctx):
        """merge_worktree rejects non-existent paths."""
        result = json.loads(await merge_worktree("/nonexistent/path", "dev"))
        assert "error" in result

    @pytest.mark.anyio
    async def test_merge_worktree_rejects_non_worktree(self, tool_ctx, tmp_path):
        """merge_worktree rejects paths that aren't git worktrees."""
        result = json.loads(await merge_worktree(str(tmp_path), "dev"))
        assert "error" in result


class TestMergeWorktreeNoBypass:
    """merge_worktree always runs its own test gate — no bypass possible."""

    @pytest.mark.anyio
    async def test_skip_test_gate_parameter_rejected(self):
        """merge_worktree does not accept skip_test_gate parameter."""
        result = json.loads(await merge_worktree("/tmp/wt", "dev", skip_test_gate=True))
        assert result["success"] is False
        assert result["subtype"] == "tool_exception"
        assert "skip_test_gate" in result["error"]

    @pytest.mark.anyio
    async def test_internal_gate_cross_validates_output(self, tool_ctx, tmp_path):
        """merge_worktree's internal gate catches rc=0 with failure text."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(
            _make_result(0, "= 3 failed, 97 passed =", "")
        )  # test-check: rc=0 but failed text
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

    @pytest.mark.anyio
    async def test_gate_failure_does_not_expose_summary(self, tool_ctx, tmp_path):
        """When gate blocks, response contains no test output details."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(1, "= 3 failed, 97 passed =", ""))  # test-check
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert "test_summary" not in result


class TestMergeWorktreeCleanupReporting:
    """merge_worktree reports accurate cleanup results."""

    @pytest.mark.anyio
    async def test_reports_worktree_remove_failure(self, tool_ctx, tmp_path):
        """3a: worktree_removed reflects actual worktree removal result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        with patch(
            "autoskillit.server.git.remove_git_worktree",
            new=AsyncMock(
                return_value=CleanupResult(failed=[(str(wt), "error: untracked files")])
            ),
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is False

    @pytest.mark.anyio
    async def test_reports_branch_delete_failure(self, tool_ctx, tmp_path):
        """3b: branch_deleted reflects actual git branch -D result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(1, "", "error: branch not found"))  # branch -D FAILS
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is False

    @pytest.mark.anyio
    async def test_checks_fetch_result(self, tool_ctx, tmp_path):
        """3c: git fetch failure returns error before rebase attempt."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(
            _make_result(1, "", "fatal: could not connect to remote")
        )  # git fetch FAILS
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.FETCH


class TestMergeWorktreeCleanupWarnings:
    """merge_worktree emits logger.warning when cleanup steps fail post-merge."""

    @pytest.mark.anyio
    async def test_warns_on_worktree_remove_failure(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D

        with (
            patch(
                "autoskillit.server.git.remove_git_worktree",
                new=AsyncMock(
                    return_value=CleanupResult(failed=[(str(wt), "error: untracked files")])
                ),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["worktree_removed"] is False
        warning_entries = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any(entry.get("operation") == "worktree_remove" for entry in warning_entries)

    @pytest.mark.anyio
    async def test_warns_on_branch_delete_failure(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(1, "", "error: branch not found"))  # branch -D FAILS

        with structlog.testing.capture_logs() as logs:
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is False
        assert result["branch_deleted"] is False
        warning_entries = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any(entry.get("operation") == "branch_delete" for entry in warning_entries)

    @pytest.mark.anyio
    async def test_no_warning_on_clean_cleanup(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove — success
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D — success

        with structlog.testing.capture_logs() as logs:
            result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is True
        cleanup_warnings = [
            entry
            for entry in logs
            if entry.get("log_level") == "warning" and "cleanup" in str(entry.get("event", ""))
        ]
        assert cleanup_warnings == []


class TestMergeWorktreeRemoteTrackingGuard:
    """merge_worktree diagnoses unpublished base branch after fetch."""

    @pytest.mark.anyio
    async def test_merge_worktree_diagnoses_unpublished_base_branch(
        self, tool_ctx: object, tmp_path: Path
    ) -> None:
        """merge_worktree returns BASE_NOT_PUBLISHED error when ref is absent after fetch."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))  # rev-parse
        tool_ctx.runner.push(_make_result(stdout="impl/task-01"))  # branch --show-current
        tool_ctx.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(stdout="PASS\n= 100 passed ="))  # test check
        tool_ctx.runner.push(_make_result())  # git fetch origin
        # Step 5.5: ref check fails — branch not on remote
        tool_ctx.runner.push(
            _make_result(returncode=128, stderr="fatal: Needed a single revision")
        )

        result = json.loads(await merge_worktree(str(wt), "feature/local-only"))

        assert result["failed_step"] == "pre_rebase_check"
        assert result["state"] == "worktree_intact_base_not_published"
        assert "feature/local-only" in result["error"]
        assert "push" in result["error"].lower()
        assert result["worktree_path"] == str(wt)

    @pytest.mark.anyio
    async def test_merge_worktree_unpublished_base_reports_pre_rebase_check_step(
        self, tool_ctx: object, tmp_path: Path
    ) -> None:
        """Step 5.5 failure must report failed_step=PRE_REBASE_CHECK, not REBASE."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))  # rev-parse
        tool_ctx.runner.push(_make_result(stdout="feat/x"))  # branch
        tool_ctx.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(stdout="PASS\n= 5 passed ="))  # test-check
        tool_ctx.runner.push(_make_result())  # fetch
        tool_ctx.runner.push(
            _make_result(returncode=128, stderr="fatal: Needed a single revision")
        )  # step 5.5

        result = json.loads(await merge_worktree(str(wt), "local-only-branch"))

        assert result["failed_step"] == "pre_rebase_check"  # renamed from "rebase"
        assert result["state"] == "worktree_intact_base_not_published"

    @pytest.mark.anyio
    async def test_merge_worktree_fatal_invalid_upstream_produces_rebase_aborted(
        self, tool_ctx: object, tmp_path: Path
    ) -> None:
        """Regression: git rebase fatal: invalid upstream is caught as rebase failure."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))
        tool_ctx.runner.push(_make_result(stdout="impl/task-01"))
        tool_ctx.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(stdout="PASS\n= 100 passed ="))  # test gate
        tool_ctx.runner.push(_make_result())  # fetch
        tool_ctx.runner.push(_make_result())  # ref check passes
        tool_ctx.runner.push(_make_result())  # git log --merges (no merge commits — step 5.6)
        # Rebase fails with fatal: invalid upstream (bypassed guard scenario)
        tool_ctx.runner.push(
            _make_result(
                returncode=128, stderr="fatal: invalid upstream 'origin/feature/local-only'"
            )
        )
        tool_ctx.runner.push(_make_result())  # rebase --abort

        result = json.loads(await merge_worktree(str(wt), "feature/local-only"))

        assert result["failed_step"] == "rebase"
        assert result["state"] == "worktree_intact_rebase_aborted"
        assert "invalid upstream" in result["stderr"]


class TestMergeWorktreeTiming:
    """merge_worktree records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_merge_worktree_step_name_records_timing(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")
        tool_ctx.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))
        tool_ctx.runner.push(_make_result(stdout="impl/task-01"))
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result(stdout="PASS\n= 100 passed ="))
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())  # git log --merges (step 5.6)
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())

        await merge_worktree(str(wt), "dev", step_name="merge")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "merge" for e in report)

    @pytest.mark.anyio
    async def test_merge_worktree_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")
        tool_ctx.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))
        tool_ctx.runner.push(_make_result(stdout="impl/task-01"))
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result(stdout="PASS\n= 100 passed ="))
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())  # git log --merges (step 5.6)
        tool_ctx.runner.push(_make_result())
        tool_ctx.runner.push(_make_result())

        await merge_worktree(str(wt), "dev")
        assert tool_ctx.timing_log.get_report() == []


class TestClassifyFixTiming:
    """classify_fix records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_classify_fix_step_name_records_timing(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(stdout="src/other/file.py\n"))
        await classify_fix(str(tmp_path), "main", step_name="classify")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "classify" for e in report)

    @pytest.mark.anyio
    async def test_classify_fix_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(stdout="src/other/file.py\n"))
        await classify_fix(str(tmp_path), "main")
        assert tool_ctx.timing_log.get_report() == []


class TestMergeWorktreeMergeCommitDetection:
    """merge_worktree detects merge commits before rebase and returns actionable error."""

    @pytest.mark.anyio
    async def test_detects_merge_commits_before_rebase(self, tool_ctx, tmp_path):
        """Step 5.6: merge commits in worktree history abort before rebase with specific error."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        # Step 5.6: git log --merges finds merge commits
        tool_ctx.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["failed_step"] == MergeFailedStep.MERGE_COMMITS_DETECTED
        assert result["state"] == MergeState.WORKTREE_INTACT_MERGE_COMMITS_DETECTED
        assert "merge_commits" in result
        assert result["merge_commits"] == ["bb481aa Merge PR branch"]

    @pytest.mark.anyio
    async def test_merge_commit_error_message_is_actionable(self, tool_ctx, tmp_path):
        """Step 5.6: error message names cherry-pick, checkout, and forbids run_cmd bypass."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "dev"))

        assert "cherry-pick" in result["error"]
        assert "checkout" in result["error"]
        assert result["worktree_path"] == str(wt)
        assert "run_cmd" in result["error"]

    @pytest.mark.anyio
    async def test_linear_history_passes_merge_commit_check(self, tool_ctx, tmp_path):
        """Step 5.6: empty git log --merges output allows pipeline to continue to rebase."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git log --merges returns empty (step 5.6)
        tool_ctx.runner.push(_make_result(1, "", "CONFLICT (content): ..."))  # rebase fails
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase --abort

        result = json.loads(await merge_worktree(str(wt), "dev"))

        # Pipeline passed step 5.6 and reached rebase — failed there, not at step 5.6
        assert result["failed_step"] == MergeFailedStep.REBASE
        assert result["state"] == MergeState.WORKTREE_INTACT_REBASE_ABORTED


class TestCreateUniqueBranch:
    @pytest.mark.anyio
    async def test_creates_branch_when_unique(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current (HEAD state)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True

    @pytest.mark.anyio
    async def test_appends_suffix_when_branch_exists_on_remote(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "abc123\trefs/heads/feat-foo-42\n", ""))  # exists
        tool_ctx.runner.push(_make_result(0, "", ""))  # -2 not found
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current (HEAD state)
        tool_ctx.runner.push(_make_result(0, "", ""))  # checkout -b feat-foo-42-2
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42-2"
        assert result["was_unique"] is False

    @pytest.mark.anyio
    async def test_ls_remote_auth_failure_falls_back_gracefully(self, tool_ctx):
        tool_ctx.runner.push(_make_result(128, "", "fatal: Authentication failed"))
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current (HEAD state)
        tool_ctx.runner.push(_make_result(0, "", ""))  # checkout proceeds with base name
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True

    @pytest.mark.anyio
    async def test_no_issue_uses_slug_only(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # checkout -b
        result = json.loads(await create_unique_branch("feat-bar", None, "origin", "."))
        assert result["branch_name"] == "feat-bar"

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await create_unique_branch("foo", 1, "origin", "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_timing_recorded_when_step_name_provided(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # checkout -b
        await create_unique_branch("feat-x", 1, "origin", ".", step_name="branch_step")
        assert any(e["step_name"] == "branch_step" for e in tool_ctx.timing_log.get_report())

    @pytest.mark.anyio
    async def test_create_unique_branch_uses_base_branch_name_when_provided(self, tool_ctx):
        """AP3: when base_branch_name is provided, use it directly as the base."""
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch(base_branch_name="impl/238", cwd="."))
        assert result["branch_name"] == "impl/238"
        # ls-remote must check the exact base_branch_name, not slug-issue composition
        ls_remote_cmd = next(
            (args[0] for args in tool_ctx.runner.call_args_list if "ls-remote" in args[0]),
            None,
        )
        assert ls_remote_cmd is not None, "No ls-remote subprocess call found"
        assert any("impl/238" in arg for arg in ls_remote_cmd)

    @pytest.mark.anyio
    async def test_reports_base_ref_in_return_value(self, tool_ctx):
        """Test 1.6: create_unique_branch reports base_ref (the branch HEAD was on)."""
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx.runner.push(_make_result(0, "feature-branch\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True
        assert result["base_ref"] == "feature-branch"

    @pytest.mark.anyio
    async def test_reports_detached_head_as_base_ref(self, tool_ctx):
        """create_unique_branch reports DETACHED_HEAD when HEAD is detached."""
        tool_ctx.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch --show-current returns empty
        tool_ctx.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["base_ref"] == "DETACHED_HEAD"


class TestCheckPrMergeable:
    @pytest.mark.anyio
    async def test_mergeable_pr(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is True
        assert result["merge_state_status"] == "CLEAN"
        assert result["mergeable_status"] == "MERGEABLE"

    @pytest.mark.anyio
    async def test_conflicting_pr_is_not_mergeable(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is False
        assert result["merge_state_status"] == "DIRTY"
        assert result["mergeable_status"] == "CONFLICTING"

    @pytest.mark.anyio
    async def test_unknown_mergeable_status_returned_raw(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is False  # UNKNOWN != MERGEABLE → False
        assert result["mergeable_status"] == "UNKNOWN"

    @pytest.mark.anyio
    async def test_gh_command_failure_returns_error(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "pr not found"))
        result = json.loads(await check_pr_mergeable(99, "."))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await check_pr_mergeable(1, "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_repo_flag_passed_to_gh(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, ".", repo="owner/myrepo"))
        call_cmd = tool_ctx.runner.call_args_list[-1][0]
        assert "-R" in call_cmd
        assert result["mergeable"] is True

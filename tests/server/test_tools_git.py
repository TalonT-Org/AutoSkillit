"""Tests for merge_worktree core flow: happy path, test gate, rebase abort, bypass prevention."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.core.types import MergeFailedStep, MergeState
from autoskillit.server.tools.tools_git import merge_worktree
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestMergeWorktree:
    """merge_worktree enforces test gate, rebases, and merges."""

    @pytest.mark.anyio
    async def test_merge_worktree_blocks_on_failing_tests(self, tool_ctx_kitchen_open, tmp_path):
        """merge_worktree returns error with failed_step when test-check fails."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse --git-dir
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "impl-branch\n", "")
        )  # branch --show-current
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "FAIL\n= 3 failed, 97 passed =", "")
        )  # test-check
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE
        assert result["state"] == MergeState.WORKTREE_INTACT
        assert "test_summary" not in result

    @pytest.mark.anyio
    async def test_merge_worktree_merges_on_green(self, tool_ctx_kitchen_open, tmp_path):
        """merge_worktree performs rebase+merge when tests pass."""
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
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # branch -D
        with patch("autoskillit.server.git.resolve_main_worktree", return_value=Path("/repo")):
            result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True
        assert result["into_branch"] == "dev"
        assert result["cleanup_succeeded"] is True
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True
        # Verify merge command cwd is the main_repo (/repo)
        merge_call = next(
            args
            for args in tool_ctx_kitchen_open.runner.call_args_list
            if len(args[0]) > 1 and args[0][1] == "merge"
        )
        assert merge_call[1] == Path("/repo")

    @pytest.mark.anyio
    async def test_merge_worktree_aborts_on_rebase_failure(self, tool_ctx_kitchen_open, tmp_path):
        """merge_worktree runs rebase --abort and returns step-specific error."""
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
        )  # test-check
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\n", "")
        )  # rev-parse --verify (step 5.5)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "", "CONFLICT (content): ...")
        )  # git rebase FAILS
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git rebase --abort
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
    async def test_merge_worktree_rejects_nonexistent_path(self, tool_ctx_kitchen_open):
        """merge_worktree rejects non-existent paths."""
        result = json.loads(await merge_worktree("/nonexistent/path", "dev"))
        assert "error" in result

    @pytest.mark.anyio
    async def test_merge_worktree_rejects_non_worktree(self, tool_ctx_kitchen_open, tmp_path):
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
    async def test_internal_gate_cross_validates_output(self, tool_ctx_kitchen_open, tmp_path):
        """merge_worktree's internal gate catches rc=0 with failure text."""
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
            _make_result(0, "= 3 failed, 97 passed =", "")
        )  # test-check: rc=0 but failed text
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

    @pytest.mark.anyio
    async def test_gate_failure_does_not_expose_summary(self, tool_ctx_kitchen_open, tmp_path):
        """When gate blocks, response contains no test output details."""
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
            _make_result(1, "= 3 failed, 97 passed =", "")
        )  # test-check
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert "error" in result
        assert "test_summary" not in result

    @pytest.mark.anyio
    async def test_gate_failure_truncates_large_output(self, tool_ctx_kitchen_open, tmp_path):
        """merge_worktree truncates test_stdout and test_stderr in failure response."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse
        tool_ctx_kitchen_open.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git ls-files
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git status --porcelain
        large_stdout = "F" * 100_000 + "\n= 3 failed, 97 passed ="
        large_stderr = "E" * 100_000
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, large_stdout, large_stderr)
        )  # test-check
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["failed_step"] == MergeFailedStep.TEST_GATE
        assert len(result["test_stdout"]) <= 5100
        assert len(result["test_stderr"]) <= 5100

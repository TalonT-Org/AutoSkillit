"""Tests for merge_worktree remote tracking guard, timing, and merge commit detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.types import MergeFailedStep, MergeState
from autoskillit.server.tools.tools_git import merge_worktree
from tests.conftest import _make_result
from tests.server.conftest import assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestMergeWorktreeRemoteTrackingGuard:
    """merge_worktree diagnoses unpublished base branch after fetch."""

    @pytest.mark.anyio
    async def test_merge_worktree_diagnoses_unpublished_base_branch(
        self, tool_ctx_kitchen_open: object, tmp_path: Path
    ) -> None:
        """merge_worktree returns BASE_NOT_PUBLISHED error when ref is absent after fetch."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(stdout="/repo/.git/worktrees/wt")
        )  # rev-parse
        tool_ctx_kitchen_open.runner.push(
            _make_result(stdout="impl/task-01")
        )  # branch --show-current
        tool_ctx_kitchen_open.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(
            _make_result(stdout="PASS\n= 100 passed =")
        )  # test check
        tool_ctx_kitchen_open.runner.push(_make_result())  # git fetch origin
        # Step 5.5: ref check fails — branch not on remote
        tool_ctx_kitchen_open.runner.push(
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
        self, tool_ctx_kitchen_open: object, tmp_path: Path
    ) -> None:
        """Step 5.5 failure must report failed_step=PRE_REBASE_CHECK, not REBASE."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(
            _make_result(stdout="/repo/.git/worktrees/wt")
        )  # rev-parse
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="feat/x"))  # branch
        tool_ctx_kitchen_open.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="PASS\n= 5 passed ="))  # test-check
        tool_ctx_kitchen_open.runner.push(_make_result())  # fetch
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=128, stderr="fatal: Needed a single revision")
        )  # step 5.5

        result = json.loads(await merge_worktree(str(wt), "local-only-branch"))

        assert result["failed_step"] == "pre_rebase_check"  # renamed from "rebase"
        assert result["state"] == "worktree_intact_base_not_published"

    @pytest.mark.anyio
    async def test_merge_worktree_fatal_invalid_upstream_produces_rebase_aborted(
        self, tool_ctx_kitchen_open: object, tmp_path: Path
    ) -> None:
        """Regression: git rebase fatal: invalid upstream is caught as rebase failure."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx_kitchen_open.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="impl/task-01"))
        tool_ctx_kitchen_open.runner.push(_make_result())  # git ls-files (pre-dirty-tree check)
        tool_ctx_kitchen_open.runner.push(_make_result())  # git status --porcelain (clean)
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="PASS\n= 100 passed ="))  # test gate
        tool_ctx_kitchen_open.runner.push(_make_result())  # fetch
        tool_ctx_kitchen_open.runner.push(_make_result())  # ref check passes
        tool_ctx_kitchen_open.runner.push(
            _make_result()
        )  # git log --merges (no merge commits — step 5.6)
        # Rebase fails with fatal: invalid upstream (bypassed guard scenario)
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                returncode=128, stderr="fatal: invalid upstream 'origin/feature/local-only'"
            )
        )
        tool_ctx_kitchen_open.runner.push(_make_result())  # rebase --abort

        result = json.loads(await merge_worktree(str(wt), "feature/local-only"))

        assert result["failed_step"] == "rebase"
        assert result["state"] == "worktree_intact_rebase_aborted"
        assert "invalid upstream" in result["stderr"]


class TestMergeWorktreeTiming:
    """merge_worktree records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_merge_worktree_step_name_records_timing(self, tool_ctx_kitchen_open, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="/repo/.git/worktrees/wt"))
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="impl/task-01"))
        tool_ctx_kitchen_open.runner.push(_make_result())
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="PASS\n= 100 passed ="))
        tool_ctx_kitchen_open.runner.push(_make_result())
        tool_ctx_kitchen_open.runner.push(_make_result())
        tool_ctx_kitchen_open.runner.push(_make_result())  # git log --merges (step 5.6)
        tool_ctx_kitchen_open.runner.push(_make_result())
        tool_ctx_kitchen_open.runner.push(_make_result())

        await merge_worktree(str(wt), "dev", step_name="merge")
        assert_step_timed(tool_ctx_kitchen_open.timing_log, "merge")

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
        assert_no_timing(tool_ctx.timing_log)


class TestMergeWorktreeMergeCommitDetection:
    """merge_worktree detects merge commits before rebase and returns actionable error."""

    @pytest.mark.anyio
    async def test_detects_merge_commits_before_rebase(self, tool_ctx_kitchen_open, tmp_path):
        """Step 5.6: merge commits in worktree history abort before rebase with specific error."""
        wt = tmp_path / "wt"
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
        # Step 5.6: git log --merges finds merge commits
        tool_ctx_kitchen_open.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "dev"))

        assert result["failed_step"] == MergeFailedStep.MERGE_COMMITS_DETECTED
        assert result["state"] == MergeState.WORKTREE_INTACT_MERGE_COMMITS_DETECTED
        assert "merge_commits" in result
        assert result["merge_commits"] == ["bb481aa Merge PR branch"]

    @pytest.mark.anyio
    async def test_merge_commit_error_message_is_actionable(self, tool_ctx_kitchen_open, tmp_path):
        """Step 5.6: error message names cherry-pick, checkout, and forbids run_cmd bypass."""
        wt = tmp_path / "wt"
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
        tool_ctx_kitchen_open.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "dev"))

        assert "cherry-pick" in result["error"]
        assert "checkout" in result["error"]
        assert result["worktree_path"] == str(wt)
        assert "run_cmd" in result["error"]

    @pytest.mark.anyio
    async def test_linear_history_passes_merge_commit_check(self, tool_ctx_kitchen_open, tmp_path):
        """Step 5.6: empty git log --merges output allows pipeline to continue to rebase."""
        wt = tmp_path / "wt"
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
        tool_ctx_kitchen_open.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # git log --merges returns empty (step 5.6)
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "", "CONFLICT (content): ...")
        )  # rebase fails
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # rebase --abort

        result = json.loads(await merge_worktree(str(wt), "dev"))

        # Pipeline passed step 5.6 and reached rebase — failed there, not at step 5.6
        assert result["failed_step"] == MergeFailedStep.REBASE
        assert result["state"] == MergeState.WORKTREE_INTACT_REBASE_ABORTED

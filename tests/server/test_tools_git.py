"""Tests for classify_fix and merge_worktree MCP tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog.testing

from autoskillit.config import AutomationConfig, ClassifyFixConfig
from autoskillit.core.types import MergeFailedStep, MergeState, RestartScope
from autoskillit.server.tools_git import classify_fix, merge_worktree
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
    async def test_critical_files_return_full_restart(self, tool_ctx):
        changed = "src/core/handler.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert len(result["critical_files"]) == 1
        assert result["critical_files"][0] == "src/core/handler.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_non_critical_returns_partial_restart(self, tool_ctx):
        changed = "src/workers/runner.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["critical_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_git_diff_failure(self, tool_ctx):
        tool_ctx.runner.push(_make_result(128, "", "fatal: bad revision"))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert "restart_scope" in result
        assert "Cannot diff" in result["reason"]

    @pytest.mark.anyio
    async def test_critical_path_in_diff_triggers_full_restart(self, tool_ctx):
        changed = "src/api/routes.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART


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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(1, "FAIL\n= 3 failed, 97 passed =", ""))  # test-check
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n\n"
                "worktree /wt\nHEAD def456\nbranch refs/heads/impl-branch\n\n",
                "",
            )
        )  # worktree list --porcelain
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["merge_succeeded"] is True
        assert result["cleanup_succeeded"] is True
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True

    @pytest.mark.anyio
    async def test_merge_worktree_aborts_on_rebase_failure(self, tool_ctx, tmp_path):
        """merge_worktree runs rebase --abort and returns step-specific error."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(1, "", "CONFLICT (content): ..."))  # git rebase FAILS
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase --abort
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        result = json.loads(await merge_worktree("/nonexistent/path", "main"))
        assert "error" in result

    @pytest.mark.anyio
    async def test_merge_worktree_rejects_non_worktree(self, tool_ctx, tmp_path):
        """merge_worktree rejects paths that aren't git worktrees."""
        result = json.loads(await merge_worktree(str(tmp_path), "main"))
        assert "error" in result


class TestMergeWorktreeNoBypass:
    """merge_worktree always runs its own test gate — no bypass possible."""

    @pytest.mark.anyio
    async def test_skip_test_gate_parameter_rejected(self):
        """merge_worktree does not accept skip_test_gate parameter."""
        with pytest.raises(TypeError, match="skip_test_gate"):
            await merge_worktree("/tmp/wt", "main", skip_test_gate=True)

    @pytest.mark.anyio
    async def test_internal_gate_cross_validates_output(self, tool_ctx, tmp_path):
        """merge_worktree's internal gate catches rc=0 with failure text."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(
            _make_result(0, "= 3 failed, 97 passed =", "")
        )  # test-check: rc=0 but failed text
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert "test_summary" not in result


class TestMergeWorktreeCleanupReporting:
    """merge_worktree reports accurate cleanup results."""

    @pytest.mark.anyio
    async def test_reports_worktree_remove_failure(self, tool_ctx, tmp_path):
        """3a: worktree_removed reflects actual git worktree remove result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(
            _make_result(1, "", "error: untracked files")
        )  # worktree remove FAILS
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(1, "", "error: branch not found"))  # branch -D FAILS
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(
            _make_result(1, "", "fatal: could not connect to remote")
        )  # git fetch FAILS
        result = json.loads(await merge_worktree(str(wt), "main"))
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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(
            _make_result(1, "", "error: untracked files")
        )  # worktree remove FAILS
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D

        with structlog.testing.capture_logs() as logs:
            result = json.loads(await merge_worktree(str(wt), "main"))

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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(1, "", "error: branch not found"))  # branch -D FAILS

        with structlog.testing.capture_logs() as logs:
            result = json.loads(await merge_worktree(str(wt), "main"))

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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # pre-rebase test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (no merge commits — step 5.6)
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # post-rebase test-check
        tool_ctx.runner.push(
            _make_result(0, "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n", "")
        )
        tool_ctx.runner.push(_make_result(0, "", ""))  # merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove — success
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D — success

        with structlog.testing.capture_logs() as logs:
            result = json.loads(await merge_worktree(str(wt), "main"))

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

        await merge_worktree(str(wt), "main", step_name="merge")
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

        await merge_worktree(str(wt), "main")
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
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        # Step 5.6: git log --merges finds merge commits
        tool_ctx.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "main"))

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
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx.runner.push(_make_result(0, "bb481aa Merge PR branch\n", ""))

        result = json.loads(await merge_worktree(str(wt), "main"))

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
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # git log --merges returns empty (step 5.6)
        tool_ctx.runner.push(_make_result(1, "", "CONFLICT (content): ..."))  # rebase fails
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase --abort

        result = json.loads(await merge_worktree(str(wt), "main"))

        # Pipeline passed step 5.6 and reached rebase — failed there, not at step 5.6
        assert result["failed_step"] == MergeFailedStep.REBASE
        assert result["state"] == MergeState.WORKTREE_INTACT_REBASE_ABORTED

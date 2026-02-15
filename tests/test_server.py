"""Tests for automation_mcp server MCP tools."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from automation_mcp.process_lifecycle import SubprocessResult
from automation_mcp.server import (
    EXECUTOR_PRESERVE_DIRS,
    PLANNER_PATH_PREFIXES,
    _check_dry_walkthrough,
    _parse_pytest_summary,
    _run_subprocess,
    classify_fix,
    merge_worktree,
    reset_executor,
    reset_test_dir,
    run_cmd,
    run_skill_retry,
    test_check,
)


def _make_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        pid=12345,
    )


def _make_timeout_result(stdout: str = "", stderr: str = ""):
    """Create a timed-out SubprocessResult."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr=stderr,
        timed_out=True,
        pid=12345,
    )


class TestRunCmd:
    """T1, T2: run_cmd executes commands and returns exit code semantics."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_successful_command(self, mock_run):
        mock_run.return_value = _make_result(0, "hello\n", "")
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["bash", "-c", "echo hello"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_failing_command(self, mock_run):
        mock_run.return_value = _make_result(1, "", "error")
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp"))

        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_custom_timeout(self, mock_run):
        mock_run.return_value = _make_result(0, "", "")
        await run_cmd(cmd="sleep 1", cwd="/tmp", timeout=30)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 30.0


class TestRunPlannerRemoved:
    """T3: run_planner tool no longer exists."""

    def test_run_planner_not_importable(self):
        from automation_mcp import server

        assert not hasattr(server, "run_planner")

    def test_run_cmd_exists(self):
        from automation_mcp import server

        assert hasattr(server, "run_cmd")


class TestClassifyFix:
    """T4, T5: classify_fix returns correct restart scope based on changed files."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_planner_files_return_restart_plan(self, mock_run):
        changed = "agents/graph/planner/nodes/create.py\npackages/sdk/core.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == "restart_plan"
        assert len(result["planner_files"]) == 1
        assert result["planner_files"][0] == "agents/graph/planner/nodes/create.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_executor_only_returns_restart_executor(self, mock_run):
        changed = "agents/graph/executor/nodes/run.py\npackages/sdk/core.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == "restart_executor"
        assert result["planner_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_git_diff_failure(self, mock_run):
        mock_run.return_value = _make_result(128, "", "fatal: bad revision")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert "error" in result
        assert "git diff failed" in result["error"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_planner_test_files_trigger_restart_plan(self, mock_run):
        changed = "tests/agents/graph/planner/test_nodes.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == "restart_plan"

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_all_planner_prefixes_recognized(self, mock_run):
        for prefix in PLANNER_PATH_PREFIXES:
            mock_run.return_value = _make_result(0, f"{prefix}some_file.py\n", "")
            result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))
            assert result["restart_scope"] == "restart_plan", f"Failed for {prefix}"


class TestResetExecutor:
    """T6, T7: reset_executor preserves .agent_data and plans, rejects non-playground."""

    @pytest.mark.asyncio
    async def test_rejects_non_playground_path(self):
        result = json.loads(await reset_executor(test_dir="/home/talon/projects/helper_agents"))
        assert result["error"] == "Safety: only playground directories allowed"

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_directory(self, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        result = json.loads(await reset_executor(test_dir=str(playground_dir)))
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_preserves_agent_data_and_plans(self, mock_run, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)

        (playground_dir / ".agent_data").mkdir()
        (playground_dir / ".agent_data" / "agent_data.db").touch()
        (playground_dir / "plans").mkdir()
        (playground_dir / "plans" / "plan.json").touch()
        (playground_dir / "output.txt").touch()
        (playground_dir / "temp_dir").mkdir()
        (playground_dir / "temp_dir" / "file.txt").touch()

        mock_run.return_value = _make_result(0, "", "")

        result = json.loads(await reset_executor(test_dir=str(playground_dir)))

        assert result["success"] is True
        assert ".agent_data" in result["preserved"]
        assert "plans" in result["preserved"]
        assert "output.txt" in result["deleted"]
        assert "temp_dir" in result["deleted"]

        assert (playground_dir / ".agent_data" / "agent_data.db").exists()
        assert (playground_dir / "plans" / "plan.json").exists()
        assert not (playground_dir / "output.txt").exists()
        assert not (playground_dir / "temp_dir").exists()

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_reset_status_failure(self, mock_run, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)

        mock_run.return_value = _make_result(1, "", "command not found")

        result = json.loads(await reset_executor(test_dir=str(playground_dir)))

        assert "error" in result
        assert result["error"] == "ai-executor reset-status failed"
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_runs_correct_reset_command(self, mock_run, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)

        mock_run.return_value = _make_result(0, "", "")

        await reset_executor(test_dir=str(playground_dir))

        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "ai-executor",
            "reset-status",
            "--force",
            "--no-backup",
        ]


class TestCheckDryWalkthrough:
    """Dry-walkthrough gate blocks both /implement-worktree variants."""

    def test_dry_walkthrough_gate_blocks_implement_no_merge(self, tmp_path):
        """Gate blocks /implement-worktree-no-merge when plan lacks marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("# My Plan\n\nSome content")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert "error" in parsed
        assert "dry-walked" in parsed["error"].lower()

    def test_dry_walkthrough_gate_passes_implement_no_merge(self, tmp_path):
        """Gate allows /implement-worktree-no-merge when plan has marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# My Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is None

    def test_dry_walkthrough_gate_still_works_for_implement_worktree(self, tmp_path):
        """Original /implement-worktree gating is not broken."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = _check_dry_walkthrough(f"/implement-worktree {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert "error" in parsed

    def test_dry_walkthrough_gate_ignores_unrelated_skills(self):
        """Gate ignores skills that are not implement-worktree variants."""
        result = _check_dry_walkthrough("/investigate some-error", "/tmp")
        assert result is None


class TestMergeWorktree:
    """merge_worktree enforces test gate, rebases, and merges."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_merge_worktree_blocks_on_failing_tests(self, mock_run, tmp_path):
        """merge_worktree returns error with failed_step when test-check fails."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse --git-dir
            (0, "impl-branch\n", ""),  # branch --show-current
            (1, "FAIL\n3 failed, 97 passed", ""),  # test-check
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == "test_gate"
        assert result["state"] == "worktree_intact"
        assert "test_summary" not in result

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_merge_worktree_merges_on_green(self, mock_run, tmp_path):
        """merge_worktree performs rebase+merge when tests pass."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "PASS\n100 passed", ""),  # test-check
            (0, "", ""),  # git fetch
            (0, "", ""),  # git rebase
            (0, "", ""),  # git diff (no-op rebase)
            (
                0,
                "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n\n"
                "worktree /wt\nHEAD def456\nbranch refs/heads/impl-branch\n\n",
                "",
            ),  # worktree list --porcelain
            (0, "", ""),  # git merge
            (0, "", ""),  # worktree remove
            (0, "", ""),  # branch -D
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_merge_worktree_aborts_on_rebase_failure(self, mock_run, tmp_path):
        """merge_worktree runs rebase --abort and returns step-specific error."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "PASS\n100 passed", ""),  # test-check
            (0, "", ""),  # git fetch
            (1, "", "CONFLICT (content): ..."),  # git rebase FAILS
            (0, "", ""),  # git rebase --abort
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == "rebase"
        assert "aborted" in result["state"]

    @pytest.mark.asyncio
    async def test_merge_worktree_rejects_nonexistent_path(self):
        """merge_worktree rejects non-existent paths."""
        result = json.loads(await merge_worktree("/nonexistent/path", "main"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_merge_worktree_rejects_non_worktree(self, tmp_path):
        """merge_worktree rejects paths that aren't git worktrees."""
        result = json.loads(await merge_worktree(str(tmp_path), "main"))
        assert "error" in result


class TestRunSkillRetryGate:
    """run_skill_retry applies dry-walkthrough gate to implement skills."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_gates_implement_no_merge(self, tmp_path):
        """run_skill_retry applies dry-walkthrough gate to /implement-worktree-no-merge."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill_retry(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        )
        assert "error" in result
        assert "dry-walked" in result["error"].lower()


class TestToolRegistration:
    """All 8 tools are registered on the MCP server."""

    def test_all_eight_tools_exist(self):
        from automation_mcp.server import mcp as server

        tool_names = set()
        for route in server._tool_manager._tools.values():
            tool_names.add(route.name)

        expected = {
            "run_cmd",
            "run_skill",
            "run_skill_retry",
            "test_check",
            "reset_test_dir",
            "classify_fix",
            "reset_executor",
            "merge_worktree",
        }
        assert expected == tool_names

    def test_run_planner_not_registered(self):
        from automation_mcp.server import mcp as server

        tool_names = {route.name for route in server._tool_manager._tools.values()}
        assert "run_planner" not in tool_names


class TestResetTestDirUnchanged:
    """Verify existing reset_test_dir safety guards still work."""

    @pytest.mark.asyncio
    async def test_rejects_non_playground(self):
        result = json.loads(await reset_test_dir(test_dir="/home/user/project"))
        assert result["error"] == "Safety: only playground directories allowed"

    @pytest.mark.asyncio
    async def test_rejects_nonexistent(self, tmp_path):
        result = json.loads(await reset_test_dir(test_dir=str(tmp_path / "playground" / "nope")))
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_project_markers_without_force(self, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)
        (playground_dir / ".git").mkdir()

        result = json.loads(await reset_test_dir(test_dir=str(playground_dir)))
        assert result["error"] == "Safety: directory contains project markers"
        assert ".git" in result["markers_found"]

    @pytest.mark.asyncio
    async def test_accepts_project_markers_with_force(self, tmp_path):
        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)
        (playground_dir / ".git").mkdir()
        (playground_dir / "file.txt").touch()

        result = json.loads(await reset_test_dir(test_dir=str(playground_dir), force=True))
        assert result["success"] is True
        assert not (playground_dir / ".git").exists()
        assert not (playground_dir / "file.txt").exists()


class TestConstants:
    """Verify module-level constants are correct."""

    def test_planner_path_prefixes_are_directories(self):
        for prefix in PLANNER_PATH_PREFIXES:
            assert prefix.endswith("/"), f"{prefix} should end with /"

    def test_executor_preserve_dirs(self):
        assert EXECUTOR_PRESERVE_DIRS == {".agent_data", "plans"}


class TestRunSubprocessDelegatesToManaged:
    """Verify _run_subprocess delegates to run_managed_async correctly."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_normal_completion(self, mock_run):
        mock_run.return_value = _make_result(0, "output", "")
        rc, stdout, stderr = await _run_subprocess(["echo", "hi"], cwd="/tmp", timeout=10)
        assert rc == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_timeout_returns_minus_one(self, mock_run):
        mock_run.return_value = _make_timeout_result()
        rc, stdout, stderr = await _run_subprocess(["sleep", "999"], cwd="/tmp", timeout=1)
        assert rc == -1
        assert "timed out" in stderr


class TestTestCheck:
    """test_check returns unambiguous PASS/FAIL with cross-validation."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_passes_on_clean_run(self, mock_run):
        """returncode=0 with passing summary -> passed=True."""
        mock_run.return_value = _make_result(0, "100 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_fails_on_nonzero_exit(self, mock_run):
        """returncode=1 -> passed=False regardless of output."""
        mock_run.return_value = _make_result(1, "3 failed, 97 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_cross_validates_exit_code_against_output(self, mock_run):
        """returncode=0 but output contains 'failed' -> passed=False.
        This is THE bug: Taskfile PIPESTATUS fails silently, exit code is 0,
        but output clearly shows test failures."""
        mock_run.return_value = _make_result(0, "3 failed, 8538 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_does_not_expose_summary(self, mock_run):
        """test_check returns ONLY passed boolean — no summary, no output_file."""
        mock_run.return_value = _make_result(
            0, "100 passed\nTest output saved to: /tmp/out.txt\n", ""
        )
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert "summary" not in result
        assert "output_file" not in result
        assert list(result.keys()) == ["passed"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_cross_validates_error_in_output(self, mock_run):
        """returncode=0 but output contains 'error' -> passed=False."""
        mock_run.return_value = _make_result(0, "1 error, 99 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_xfailed_not_treated_as_failure(self, mock_run):
        """xfailed tests are expected failures — exit code 0, should pass."""
        mock_run.return_value = _make_result(0, "8552 passed, 3 xfailed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_xpassed_not_treated_as_failure(self, mock_run):
        """xpassed tests are unexpected passes — exit code 0, should pass."""
        mock_run.return_value = _make_result(0, "99 passed, 1 xpassed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_mixed_xfail_with_real_failure(self, mock_run):
        """Real failure + xfailed — should still fail on the real failure."""
        mock_run.return_value = _make_result(0, "1 failed, 2 xfailed, 97 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_skipped_with_exit_zero_passes(self, mock_run):
        """Skipped tests with exit 0 — parser trusts exit code."""
        mock_run.return_value = _make_result(0, "97 passed, 3 skipped\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_warnings_not_treated_as_failure(self, mock_run):
        """Warnings with exit 0 — should pass."""
        mock_run.return_value = _make_result(0, "100 passed, 5 warnings\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True


class TestRunSkillRetryApiLimit:
    """run_skill_retry correctly detects API call limit from stderr."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_detects_api_limit_on_turn_message(self, mock_run):
        """Actual Claude Code API limit message triggers hit_api_limit."""
        mock_run.return_value = _make_result(1, '{"result": ""}', "Max turns reached")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["hit_api_limit"] is True

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_no_false_positive_on_return_in_stderr(self, mock_run):
        """'return' in stderr must NOT trigger hit_api_limit."""
        mock_run.return_value = _make_result(1, '{"result": ""}', "return code error")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["hit_api_limit"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_no_false_positive_on_nocturnal(self, mock_run):
        """Compound words containing 'turn' must NOT trigger hit_api_limit."""
        mock_run.return_value = _make_result(1, '{"result": ""}', "nocturnal process failed")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["hit_api_limit"] is False


class TestParsePytestSummary:
    """_parse_pytest_summary extracts structured counts from pytest output."""

    def test_simple_pass(self):
        assert _parse_pytest_summary("100 passed\n") == {"passed": 100}

    def test_failed_and_passed(self):
        assert _parse_pytest_summary("3 failed, 97 passed\n") == {"failed": 3, "passed": 97}

    def test_xfailed_parsed_separately(self):
        counts = _parse_pytest_summary("8552 passed, 3 xfailed\n")
        assert counts == {"passed": 8552, "xfailed": 3}
        assert "failed" not in counts

    def test_mixed_all_outcomes(self):
        counts = _parse_pytest_summary("1 failed, 2 xfailed, 1 xpassed, 3 skipped, 93 passed\n")
        assert counts["failed"] == 1
        assert counts["xfailed"] == 2
        assert counts["xpassed"] == 1
        assert counts["skipped"] == 3
        assert counts["passed"] == 93

    def test_error_outcome(self):
        assert _parse_pytest_summary("1 error, 99 passed\n") == {"error": 1, "passed": 99}

    def test_multiline_finds_summary(self):
        output = "some log output\nERROR in setup\n100 passed in 2.5s\n"
        counts = _parse_pytest_summary(output)
        assert counts == {"passed": 100}

    def test_empty_output(self):
        assert _parse_pytest_summary("") == {}

    def test_no_summary_line(self):
        assert _parse_pytest_summary("no test results here\n") == {}


class TestMergeWorktreeNoBypass:
    """merge_worktree always runs its own test gate — no bypass possible."""

    @pytest.mark.asyncio
    async def test_skip_test_gate_parameter_rejected(self):
        """merge_worktree does not accept skip_test_gate parameter."""
        with pytest.raises(TypeError, match="skip_test_gate"):
            await merge_worktree("/tmp/wt", "main", skip_test_gate=True)

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_internal_gate_cross_validates_output(self, mock_run, tmp_path):
        """merge_worktree's internal gate catches rc=0 with failure text."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "3 failed, 97 passed", ""),  # test-check: rc=0 but failed text
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == "test_gate"

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_gate_failure_does_not_expose_summary(self, mock_run, tmp_path):
        """When gate blocks, response contains no test output details."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (1, "3 failed, 97 passed", ""),  # test-check
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert "test_summary" not in result

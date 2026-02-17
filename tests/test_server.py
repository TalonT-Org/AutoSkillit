"""Tests for automation_mcp server MCP tools."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from automation_mcp.config import (
    AutomationConfig,
    ClassifyFixConfig,
    ResetExecutorConfig,
    SafetyConfig,
)
from automation_mcp.process_lifecycle import SubprocessResult
from automation_mcp.server import (
    CleanupResult,
    _check_dry_walkthrough,
    _delete_directory_contents,
    _disable_tools_handler,
    _enable_tools_handler,
    _parse_pytest_summary,
    _require_enabled,
    _run_subprocess,
    classify_fix,
    merge_worktree,
    reset_executor,
    reset_test_dir,
    run_cmd,
    run_skill,
    run_skill_retry,
    test_check,
)

test_check.__test__ = False  # type: ignore[attr-defined]


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

    @pytest.fixture(autouse=True)
    def _set_prefixes(self, monkeypatch):
        """Configure planner path prefixes for classify_fix tests."""
        from automation_mcp import server

        cfg = AutomationConfig(
            classify_fix=ClassifyFixConfig(
                path_prefixes=[
                    "agents/graph/planner/",
                    "agents/prompts/planner/",
                    "apps/cli/planner/",
                    "tests/agents/graph/planner/",
                    "tests/integration/agents/planner/",
                    "tests/apps/cli/planner/",
                ]
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

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


class TestResetExecutor:
    """T6, T7: reset_executor preserves .agent_data and plans, rejects non-playground."""

    @pytest.fixture(autouse=True)
    def _set_reset_command(self, monkeypatch):
        """Configure reset_executor with a command for these tests."""
        from automation_mcp import server

        cfg = AutomationConfig(
            reset_executor=ResetExecutorConfig(
                command=["ai-executor", "reset-status", "--force", "--no-backup"]
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

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
        assert ".agent_data" in result["skipped"]
        assert "plans" in result["skipped"]
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
        assert result["error"] == "reset command failed"
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
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True

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
        from fastmcp.tools import Tool

        from automation_mcp.server import mcp as server

        tools = [c for c in server._local_provider._components.values() if isinstance(c, Tool)]
        tool_names = {t.name for t in tools}

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
        from fastmcp.tools import Tool

        from automation_mcp.server import mcp as server

        tools = [c for c in server._local_provider._components.values() if isinstance(c, Tool)]
        tool_names = {t.name for t in tools}
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


class TestConfigDefaults:
    """Verify config defaults match expected values."""

    def test_default_preserve_dirs(self):
        cfg = AutomationConfig()
        assert cfg.reset_executor.preserve_dirs == {".agent_data", "plans"}

    def test_default_test_command(self):
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["task", "test-check"]

    def test_default_classify_fix_empty_prefixes(self):
        cfg = AutomationConfig()
        assert cfg.classify_fix.path_prefixes == []


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


class TestGatedToolAccess:
    """Prompt-gated tool access: tools disabled by default, user-only activation."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self):
        """Override the global autouse fixture — start disabled for gate tests."""
        from automation_mcp import server

        server._tools_enabled = False
        yield
        server._tools_enabled = False

    @pytest.mark.asyncio
    async def test_tools_return_error_when_disabled(self):
        """All tools return error JSON when _tools_enabled is False."""
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert "error" in result
        assert "not enabled" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_tools_work_after_enable(self, mock_run):
        """After enable_tools prompt handler sets the flag, tools execute normally."""
        _enable_tools_handler()
        mock_run.return_value = _make_result(0, "hello\n", "")
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_disable_reverses_enable(self):
        """After disable_tools prompt handler, tools return error again."""
        _enable_tools_handler()
        _disable_tools_handler()
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert "error" in result

    def test_tools_disabled_by_default(self):
        """_tools_enabled defaults to False at module load."""
        from automation_mcp import server

        assert server._tools_enabled is False

    def test_prompts_registered(self):
        """enable_tools and disable_tools prompts are registered on the server."""
        from fastmcp.prompts import Prompt

        from automation_mcp.server import mcp

        prompts = [c for c in mcp._local_provider._components.values() if isinstance(c, Prompt)]
        prompt_names = {p.name for p in prompts}
        assert prompt_names == {"enable_tools", "disable_tools"}

    def test_all_tools_still_registered(self):
        """All 8 operational tools remain registered (gated, not removed)."""
        from fastmcp.tools import Tool

        from automation_mcp.server import mcp

        tools = [c for c in mcp._local_provider._components.values() if isinstance(c, Tool)]
        tool_names = {t.name for t in tools}
        expected = {
            "run_cmd",
            "run_skill",
            "run_skill_retry",
            "test_check",
            "merge_worktree",
            "reset_test_dir",
            "classify_fix",
            "reset_executor",
        }
        assert expected == tool_names

    def test_gate_error_structure(self):
        """_require_enabled returns well-formed error JSON with activation instructions."""
        error = _require_enabled()
        assert error is not None
        parsed = json.loads(error)
        assert "error" in parsed
        assert "mcp__bugfix-loop__enable_tools" in parsed["error"]

    def test_all_tools_tagged_bugfix(self):
        """All 8 tools have the 'bugfix' tag for future visibility control."""
        from fastmcp.tools import Tool

        from automation_mcp.server import mcp

        tools = [c for c in mcp._local_provider._components.values() if isinstance(c, Tool)]
        for tool in tools:
            assert "bugfix" in tool.tags, f"{tool.name} missing 'bugfix' tag"


class TestSkillsProvider:
    """Verify SkillsDirectoryProvider is registered and exposes skill resources."""

    def test_server_has_skills_provider(self):
        """MCP server registers a SkillsDirectoryProvider."""
        from fastmcp.server.providers.skills import SkillsDirectoryProvider

        from automation_mcp.server import mcp

        has_provider = any(isinstance(p, SkillsDirectoryProvider) for p in mcp.providers)
        assert has_provider

    @pytest.mark.asyncio
    async def test_skill_resources_discoverable(self):
        """Bundled skills appear as skill:// resources via MCP."""
        from fastmcp import Client

        from automation_mcp.server import mcp

        async with Client(mcp) as client:
            resources = await client.list_resources()

        skill_uris = [r.uri for r in resources if str(r.uri).startswith("skill://")]
        assert len(skill_uris) >= 10
        assert any("investigate" in str(uri) for uri in skill_uris)

    @pytest.mark.asyncio
    async def test_skill_resource_content_readable(self):
        """Reading a skill resource returns the SKILL.md content."""
        from fastmcp import Client

        from automation_mcp.server import mcp

        async with Client(mcp) as client:
            result = await client.read_resource("skill://investigate/SKILL.md")

        content = result[0].text if hasattr(result[0], "text") else str(result[0])
        assert "investigate" in content.lower() or "investigation" in content.lower()

    def test_provider_roots_match_config_order(self):
        """Provider roots are ordered per config.skills.resolution_order."""
        from fastmcp.server.providers.skills import SkillsDirectoryProvider

        from automation_mcp.server import mcp
        from automation_mcp.skill_resolver import bundled_skills_dir

        provider = next(p for p in mcp.providers if isinstance(p, SkillsDirectoryProvider))
        root_strs = [str(r) for r in provider._roots]
        assert str(bundled_skills_dir().resolve()) in root_strs


class TestConfigDrivenBehavior:
    """S1-S10: Verify tools use config instead of hardcoded values."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_test_check_uses_config_command(self, mock_run, monkeypatch):
        """S1: test_check runs _config.test_check.command."""
        from automation_mcp import server
        from automation_mcp.config import TestCheckConfig

        cfg = AutomationConfig(test_check=TestCheckConfig(command=["pytest", "-x"], timeout=300))
        monkeypatch.setattr(server, "_config", cfg)

        mock_run.return_value = _make_result(0, "100 passed\n", "")
        await test_check(worktree_path="/tmp/wt")

        call_args = mock_run.call_args
        assert call_args[0][0] == ["pytest", "-x"]
        assert call_args[1]["timeout"] == 300

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_classify_fix_uses_config_prefixes(self, mock_run, monkeypatch):
        """S2: classify_fix uses _config.classify_fix.path_prefixes."""
        from automation_mcp import server

        cfg = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=["src/custom/"]))
        monkeypatch.setattr(server, "_config", cfg)

        changed = "src/custom/handler.py\nsrc/other/util.py\n"
        mock_run.return_value = _make_result(0, changed, "")
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == "restart_plan"
        assert "src/custom/handler.py" in result["planner_files"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_classify_fix_empty_prefixes_always_executor(self, mock_run, monkeypatch):
        """S3: Empty prefix list -> always returns restart_executor."""
        from automation_mcp import server

        cfg = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=[]))
        monkeypatch.setattr(server, "_config", cfg)

        changed = "agents/graph/planner/nodes/create.py\n"
        mock_run.return_value = _make_result(0, changed, "")
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == "restart_executor"

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_reset_executor_uses_config_command(self, mock_run, monkeypatch, tmp_path):
        """S4: reset_executor runs _config.reset_executor.command."""
        from automation_mcp import server

        cfg = AutomationConfig(reset_executor=ResetExecutorConfig(command=["make", "reset"]))
        monkeypatch.setattr(server, "_config", cfg)

        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)
        mock_run.return_value = _make_result(0, "", "")

        await reset_executor(test_dir=str(playground_dir))
        assert mock_run.call_args[0][0] == ["make", "reset"]

    @pytest.mark.asyncio
    async def test_reset_executor_not_configured_returns_error(self, monkeypatch, tmp_path):
        """S5: command=None -> returns not-configured error."""
        from automation_mcp import server

        cfg = AutomationConfig(reset_executor=ResetExecutorConfig(command=None))
        monkeypatch.setattr(server, "_config", cfg)

        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)
        result = json.loads(await reset_executor(test_dir=str(playground_dir)))

        assert result["error"] == "reset_executor not configured for this project"

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_reset_executor_uses_config_preserve_dirs(self, mock_run, monkeypatch, tmp_path):
        """S6: Preserves _config.reset_executor.preserve_dirs."""
        from automation_mcp import server

        cfg = AutomationConfig(
            reset_executor=ResetExecutorConfig(
                command=["true"],
                preserve_dirs={"keep_me"},
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

        playground_dir = tmp_path / "playground" / "project"
        playground_dir.mkdir(parents=True)
        (playground_dir / "keep_me").mkdir()
        (playground_dir / "delete_me").touch()
        mock_run.return_value = _make_result(0, "", "")

        result = json.loads(await reset_executor(test_dir=str(playground_dir)))

        assert "keep_me" in result["skipped"]
        assert "delete_me" in result["deleted"]
        assert (playground_dir / "keep_me").exists()
        assert not (playground_dir / "delete_me").exists()

    def test_dry_walkthrough_uses_config_marker(self, monkeypatch, tmp_path):
        """S7: Gate checks _config.implement_gate.marker."""
        from automation_mcp import server
        from automation_mcp.config import ImplementGateConfig

        cfg = AutomationConfig(implement_gate=ImplementGateConfig(marker="CUSTOM MARKER"))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("CUSTOM MARKER\n# Plan content")
        result = _check_dry_walkthrough(f"/implement-worktree {plan}", str(tmp_path))
        assert result is None  # passes with custom marker

        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan content")
        result = _check_dry_walkthrough(f"/implement-worktree {plan}", str(tmp_path))
        assert result is not None  # fails with old default marker

    def test_dry_walkthrough_uses_config_skill_names(self, monkeypatch, tmp_path):
        """S8: Gate checks _config.implement_gate.skill_names."""
        from automation_mcp import server
        from automation_mcp.config import ImplementGateConfig

        cfg = AutomationConfig(implement_gate=ImplementGateConfig(skill_names={"/custom-impl"}))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker")

        result = _check_dry_walkthrough(f"/custom-impl {plan}", str(tmp_path))
        assert result is not None  # /custom-impl is gated

        result = _check_dry_walkthrough(f"/implement-worktree {plan}", str(tmp_path))
        assert result is None  # /implement-worktree is NOT gated (not in skill_names)

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_merge_worktree_uses_config_test_command(self, mock_run, monkeypatch, tmp_path):
        """S9: Merge's test gate runs _config.test_check.command."""
        from automation_mcp import server
        from automation_mcp.config import TestCheckConfig

        cfg = AutomationConfig(test_check=TestCheckConfig(command=["make", "test"], timeout=120))
        monkeypatch.setattr(server, "_config", cfg)

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (1, "FAIL", ""),  # test gate fails
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["failed_step"] == "test_gate"

        # Verify the test command was ["make", "test"]
        test_call = mock_run.call_args_list[2]
        assert test_call[0][0] == ["make", "test"]

    @pytest.mark.asyncio
    async def test_require_enabled_still_gates_execution(self, monkeypatch):
        """S10: _require_enabled() defense-in-depth still works with config."""
        from automation_mcp import server

        server._tools_enabled = False
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert "error" in result
        assert "not enabled" in result["error"].lower()


# ---------------------------------------------------------------------------
# Step 1: CleanupResult contract and mid-loop failures
# ---------------------------------------------------------------------------


class TestCleanupResult:
    """CleanupResult dataclass contract."""

    def test_success_property_true_when_no_failures(self):
        """1g: success is True iff failed is empty."""
        result = CleanupResult(deleted=["a", "b"], failed=[], skipped=[])
        assert result.success is True

    def test_success_property_false_when_failures(self):
        """1g: success is False when failed is non-empty."""
        result = CleanupResult(deleted=["a"], failed=[("b", "PermissionError: ...")], skipped=[])
        assert result.success is False

    def test_to_dict_structure(self):
        """to_dict returns well-formed dict with all fields."""
        result = CleanupResult(
            deleted=["a"],
            failed=[("b", "PermissionError: denied")],
            skipped=["c"],
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["deleted"] == ["a"]
        assert d["failed"] == [{"path": "b", "error": "PermissionError: denied"}]
        assert d["skipped"] == ["c"]


class TestDeleteDirectoryContents:
    """_delete_directory_contents never-raise contract."""

    def test_continues_after_permission_error(self, tmp_path):
        """1a: PermissionError on one item does not abort the loop."""
        playground = tmp_path / "playground"
        playground.mkdir()
        (playground / "dir_a").mkdir()
        (playground / "locked_dir").mkdir()
        (playground / "file_c.txt").touch()

        # Capture real rmtree before patching
        real_rmtree = shutil.rmtree

        def selective_rmtree(path, *args, **kwargs):
            if Path(path).name == "locked_dir":
                raise PermissionError("Permission denied")
            real_rmtree(path, *args, **kwargs)

        with patch("automation_mcp.server.shutil.rmtree", side_effect=selective_rmtree):
            result = _delete_directory_contents(playground)

        assert "dir_a" in result.deleted
        assert "file_c.txt" in result.deleted
        assert any(name == "locked_dir" for name, _ in result.failed)
        assert result.success is False

    def test_file_not_found_treated_as_success(self, tmp_path):
        """1b: FileNotFoundError means item is gone = success."""
        playground = tmp_path / "playground"
        playground.mkdir()
        (playground / "ghost.txt").touch()

        # Delete the file before the cleanup function processes it
        with patch.object(Path, "unlink", side_effect=FileNotFoundError("gone")):
            with patch.object(Path, "is_dir", return_value=False):
                result = _delete_directory_contents(playground)

        assert "ghost.txt" in result.deleted
        assert result.failed == []
        assert result.success is True

    def test_preserves_specified_dirs(self, tmp_path):
        """1c: Preserved dirs are skipped, others deleted."""
        playground = tmp_path / "playground"
        playground.mkdir()
        (playground / ".agent_data").mkdir()
        (playground / "plans").mkdir()
        (playground / "output.txt").touch()
        (playground / "temp_dir").mkdir()

        result = _delete_directory_contents(playground, preserve={".agent_data", "plans"})

        assert ".agent_data" in result.skipped
        assert "plans" in result.skipped
        assert "output.txt" in result.deleted
        assert "temp_dir" in result.deleted
        assert (playground / ".agent_data").exists()
        assert (playground / "plans").exists()
        assert not (playground / "output.txt").exists()
        assert not (playground / "temp_dir").exists()

    def test_all_items_deleted_successfully(self, tmp_path):
        """1d: All succeed with no failures."""
        playground = tmp_path / "playground"
        playground.mkdir()
        (playground / "a").mkdir()
        (playground / "b").touch()
        (playground / "c").touch()

        result = _delete_directory_contents(playground)

        assert result.success is True
        assert result.failed == []
        assert len(result.deleted) == 3

    @pytest.mark.asyncio
    async def test_reset_test_dir_returns_partial_failure_json(self, tmp_path):
        """1e: reset_test_dir returns structured JSON on partial failure."""
        from automation_mcp import server

        playground = tmp_path / "playground"
        playground.mkdir()
        (playground / "ok_file").touch()

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[],
        )
        with patch.object(server, "_delete_directory_contents", return_value=mock_result):
            result = json.loads(await reset_test_dir(test_dir=str(playground), force=False))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
        assert "ok_file" in result["deleted"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_reset_executor_returns_partial_failure_json(
        self, mock_run, monkeypatch, tmp_path
    ):
        """1f: reset_executor returns structured JSON on partial failure."""
        from automation_mcp import server

        cfg = AutomationConfig(reset_executor=ResetExecutorConfig(command=["true"]))
        monkeypatch.setattr(server, "_config", cfg)

        playground = tmp_path / "playground" / "project"
        playground.mkdir(parents=True)

        mock_run.return_value = _make_result(0, "", "")

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[".agent_data"],
        )
        with patch.object(server, "_delete_directory_contents", return_value=mock_result):
            result = json.loads(await reset_executor(test_dir=str(playground)))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]


# ---------------------------------------------------------------------------
# Step 2: Safety config wiring
# ---------------------------------------------------------------------------


class TestSafetyConfigWiring:
    """Safety config fields are read at the point of enforcement."""

    @pytest.mark.asyncio
    async def test_reset_test_dir_skips_playground_guard_when_disabled(
        self, monkeypatch, tmp_path
    ):
        """2a: playground_guard=False allows non-playground paths."""
        from automation_mcp import server

        cfg = AutomationConfig(safety=SafetyConfig(playground_guard=False))
        monkeypatch.setattr(server, "_config", cfg)

        # Create a non-playground directory
        non_playground = tmp_path / "my_project"
        non_playground.mkdir()
        (non_playground / "file.txt").touch()

        result = json.loads(await reset_test_dir(test_dir=str(non_playground), force=False))
        # Should NOT return the playground safety error
        assert "error" not in result or "playground" not in result.get("error", "")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_reset_test_dir_enforces_playground_guard_when_enabled(self):
        """2b: playground_guard=True (default) blocks non-playground paths."""
        result = json.loads(await reset_test_dir(test_dir="/home/user/project"))
        assert result["error"] == "Safety: only playground directories allowed"

    @pytest.mark.asyncio
    async def test_reset_executor_respects_playground_guard_config(self, monkeypatch, tmp_path):
        """2c: reset_executor respects playground_guard config."""
        from automation_mcp import server

        cfg = AutomationConfig(
            safety=SafetyConfig(playground_guard=False),
            reset_executor=ResetExecutorConfig(command=None),
        )
        monkeypatch.setattr(server, "_config", cfg)

        non_playground = tmp_path / "my_project"
        non_playground.mkdir()

        result = json.loads(await reset_executor(test_dir=str(non_playground)))
        # Should pass playground guard but fail on "not configured"
        assert result["error"] == "reset_executor not configured for this project"

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_merge_worktree_skips_test_gate_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2d: test_gate_on_merge=False skips test execution."""
        from automation_mcp import server

        cfg = AutomationConfig(safety=SafetyConfig(test_gate_on_merge=False))
        monkeypatch.setattr(server, "_config", cfg)

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            # NO test-check call — skipped
            (0, "", ""),  # git fetch
            (0, "", ""),  # git rebase
            (
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            ),  # worktree list
            (0, "", ""),  # git merge
            (0, "", ""),  # worktree remove
            (0, "", ""),  # branch -D
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["success"] is True

        # Verify no test command was called — the 3rd call should be git fetch, not test
        third_call_cmd = mock_run.call_args_list[2][0][0]
        assert third_call_cmd == ["git", "fetch", "origin"]

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_run_skill_retry_skips_dry_walkthrough_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2e: require_dry_walkthrough=False bypasses dry-walkthrough gate."""
        from automation_mcp import server

        cfg = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        mock_run.return_value = _make_result(0, '{"result": "done"}', "")
        result = json.loads(await run_skill_retry(f"/implement-worktree {plan}", str(tmp_path)))
        # Should NOT return dry-walkthrough error
        assert "error" not in result
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_skill_enforces_dry_walkthrough_when_enabled(self, tmp_path):
        """2f: run_skill enforces dry-walkthrough gate when enabled (default)."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        result = json.loads(await run_skill(f"/implement-worktree {plan}", str(tmp_path)))
        assert "error" in result
        assert "dry-walked" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("automation_mcp.server.run_managed_async")
    async def test_run_skill_skips_dry_walkthrough_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2g: run_skill skips dry-walkthrough gate when disabled."""
        from automation_mcp import server

        cfg = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        mock_run.return_value = _make_result(0, '{"result": "done"}', "")
        result = json.loads(await run_skill(f"/implement-worktree {plan}", str(tmp_path)))
        # Should NOT return dry-walkthrough error
        assert "error" not in result


# ---------------------------------------------------------------------------
# Step 3: merge_worktree cleanup reporting
# ---------------------------------------------------------------------------


class TestMergeWorktreeCleanupReporting:
    """merge_worktree reports accurate cleanup results."""

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_reports_worktree_remove_failure(self, mock_run, tmp_path):
        """3a: worktree_removed reflects actual git worktree remove result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "PASS\n100 passed", ""),  # test-check
            (0, "", ""),  # git fetch
            (0, "", ""),  # git rebase
            (
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            ),  # worktree list
            (0, "", ""),  # git merge
            (1, "", "error: untracked files"),  # worktree remove FAILS
            (0, "", ""),  # branch -D
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["success"] is True
        assert result["worktree_removed"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_reports_branch_delete_failure(self, mock_run, tmp_path):
        """3b: branch_deleted reflects actual git branch -D result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "PASS\n100 passed", ""),  # test-check
            (0, "", ""),  # git fetch
            (0, "", ""),  # git rebase
            (
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            ),  # worktree list
            (0, "", ""),  # git merge
            (0, "", ""),  # worktree remove
            (1, "", "error: branch not found"),  # branch -D FAILS
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["success"] is True
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is False

    @pytest.mark.asyncio
    @patch("automation_mcp.server._run_subprocess")
    async def test_checks_fetch_result(self, mock_run, tmp_path):
        """3c: git fetch failure returns error before rebase attempt."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        mock_run.side_effect = [
            (0, "/repo/.git/worktrees/wt\n", ""),  # rev-parse
            (0, "impl-branch\n", ""),  # branch
            (0, "PASS\n100 passed", ""),  # test-check
            (1, "", "fatal: could not connect to remote"),  # git fetch FAILS
        ]
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == "fetch"

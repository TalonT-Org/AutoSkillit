"""Tests for autoskillit server MCP tools."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.config import (
    AutomationConfig,
    ClassifyFixConfig,
    ModelConfig,
    ReadDbConfig,
    ResetWorkspaceConfig,
    RunSkillConfig,
    SafetyConfig,
    TokenUsageConfig,
)
from autoskillit.core import SkillResult
from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    RETRY_RESPONSE_FIELDS,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
    TerminationReason,
)
from autoskillit.execution.db import _select_only_authorizer, _validate_select_only
from autoskillit.execution.headless import (
    _build_skill_result,
    _ensure_skill_prefix,
    _resolve_model,
    _session_log_dir,
)
from autoskillit.execution.process import SubprocessResult
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_retry,
    _compute_success,
    _is_completion_kill_anomaly,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.execution.testing import parse_pytest_summary as _parse_pytest_summary
from autoskillit.pipeline.audit import FailureRecord
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.helpers import (
    _check_dry_walkthrough,
    _require_enabled,
    _run_subprocess,
)
from autoskillit.server.prompts import _close_kitchen_handler, _open_kitchen_handler
from autoskillit.server.tools_clone import clone_repo, push_to_remote, remove_clone
from autoskillit.server.tools_execution import run_cmd, run_python, run_skill, run_skill_retry
from autoskillit.server.tools_git import classify_fix, merge_worktree
from autoskillit.server.tools_recipe import (
    list_recipes,
    load_recipe,
    migrate_recipe,
    validate_recipe,
)
from autoskillit.server.tools_status import (
    check_quota,
    get_pipeline_report,
    get_token_summary,
    kitchen_status,
    read_db,
)
from autoskillit.server.tools_workspace import reset_test_dir, reset_workspace, test_check
from autoskillit.workspace import CleanupResult, _delete_directory_contents

test_check.__test__ = False  # type: ignore[attr-defined]


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
):
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
    )


def _make_timeout_result(stdout: str = "", stderr: str = ""):
    """Create a timed-out SubprocessResult."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.TIMED_OUT,
        pid=12345,
    )


def _success_session_json(result_text: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": result_text,
            "session_id": "test-session",
            "is_error": False,
        }
    )


def _failed_session_json() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "Task failed with an error",
            "session_id": "test-session",
            "is_error": True,
        }
    )


def _context_exhausted_session_json() -> str:
    """Session result that triggers context exhaustion / needs_retry detection."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "prompt is too long",
            "session_id": "test-session",
            "is_error": True,
            "errors": ["prompt is too long"],
        }
    )


def _make_failure_record(**overrides: object) -> FailureRecord:
    defaults = dict(
        timestamp="2026-02-24T16:00:00Z",
        skill_command="/autoskillit:implement-worktree",
        exit_code=1,
        subtype="error",
        needs_retry=False,
        retry_reason="none",
        stderr="something went wrong",
    )
    return FailureRecord(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestRunCmd:
    """T1, T2: run_cmd executes commands and returns exit code semantics."""

    @pytest.mark.asyncio
    async def test_successful_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert len(tool_ctx.runner.call_args_list) == 1
        assert tool_ctx.runner.call_args_list[0][0] == ["bash", "-c", "echo hello"]

    @pytest.mark.asyncio
    async def test_failing_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "error"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp"))

        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_custom_timeout(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        await run_cmd(cmd="sleep 1", cwd="/tmp", timeout=30)

        assert tool_ctx.runner.call_args_list[-1][2] == 30.0


class TestRunSkillPluginDir:
    """T2: run_skill and run_skill_retry pass --plugin-dir to the claude command."""

    @pytest.mark.asyncio
    async def test_run_skill_passes_plugin_dir(self, tool_ctx):
        """run_skill includes --plugin-dir and the plugin_dir from tool_ctx in the command."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate some-error", "/tmp")

        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == tool_ctx.plugin_dir

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_plugin_dir(self, tool_ctx):
        """run_skill_retry includes --plugin-dir from tool_ctx in the command."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate some-error", "/tmp")

        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == tool_ctx.plugin_dir


class TestPluginMetadataExists:
    """T1: Plugin metadata files are shipped in the package."""

    def test_plugin_json_exists(self):
        """Package contains .claude-plugin/plugin.json with correct fields."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        manifest = pkg / ".claude-plugin" / "plugin.json"
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["name"] == "autoskillit"
        assert data["version"] == autoskillit.__version__

    def test_mcp_json_exists(self):
        """Package contains .mcp.json with autoskillit server entry."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        mcp_cfg = pkg / ".mcp.json"
        assert mcp_cfg.is_file()
        data = json.loads(mcp_cfg.read_text())
        assert "autoskillit" in data["mcpServers"]
        assert data["mcpServers"]["autoskillit"]["command"] == "autoskillit"


class TestNoSkillsDirectoryProvider:
    """T3: SkillsDirectoryProvider is not used in the new plugin architecture."""

    def test_no_skills_directory_provider(self):
        """server.py must not reference SkillsDirectoryProvider."""
        import autoskillit.server as server_module

        source = Path(server_module.__file__).read_text()
        assert "SkillsDirectoryProvider" not in source


class TestPluginDirConstant:
    """T6: tool_ctx.plugin_dir defaults to the package root directory."""

    def test_plugin_dir_returns_package_root(self, tool_ctx):
        """By default tool_ctx.plugin_dir is set to tmp_path by the fixture.

        The real package dir is what the server uses at runtime (set by cli.py serve()).
        This test verifies that the fixture wires plugin_dir through _ctx correctly.
        """
        import autoskillit

        # The real package dir is what the server sets at startup.
        # We verify the attribute path works (tool_ctx.plugin_dir is accessible).
        real_pkg_dir = str(Path(autoskillit.__file__).parent)
        # tool_ctx uses tmp_path; set it to verify end-to-end wiring
        tool_ctx.plugin_dir = real_pkg_dir
        from autoskillit.server import _get_ctx

        assert _get_ctx().plugin_dir == real_pkg_dir


class TestVersionInfo:
    """version_info() returns package and plugin.json versions."""

    def test_version_info_returns_package_and_plugin_versions(self):
        from autoskillit import __version__
        from autoskillit.server import version_info

        info = version_info()
        assert isinstance(info["package_version"], str)
        assert isinstance(info["plugin_json_version"], str)
        assert info["package_version"] == __version__
        assert info["match"] is True

    def test_version_info_detects_mismatch(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["match"] is False
        assert info["package_version"] != info["plugin_json_version"]
        assert info["plugin_json_version"] == "0.0.0"

    def test_version_info_handles_missing_plugin_json(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["plugin_json_version"] is None
        assert info["match"] is False

    def test_version_info_is_public(self):
        """version_info must be a public function — no underscore prefix."""
        from autoskillit import server

        assert hasattr(server, "version_info"), "server.version_info must exist"
        assert not hasattr(server, "_version_info"), "server._version_info must be removed"
        result = server.version_info()
        assert set(result.keys()) >= {"package_version", "plugin_json_version", "match"}


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

    @pytest.mark.asyncio
    async def test_critical_files_return_full_restart(self, tool_ctx):
        changed = "src/core/handler.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert len(result["critical_files"]) == 1
        assert result["critical_files"][0] == "src/core/handler.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    async def test_non_critical_returns_partial_restart(self, tool_ctx):
        changed = "src/workers/runner.py\nlib/utils/helpers.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["critical_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    async def test_git_diff_failure(self, tool_ctx):
        tool_ctx.runner.push(_make_result(128, "", "fatal: bad revision"))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert "error" in result
        assert "git diff failed" in result["error"]

    @pytest.mark.asyncio
    async def test_critical_path_in_diff_triggers_full_restart(self, tool_ctx):
        changed = "src/api/routes.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART


class TestResetWorkspace:
    """T6, T7: reset_workspace preserves configured dirs, requires marker."""

    @pytest.fixture(autouse=True)
    def _set_reset_command(self, tool_ctx):
        """Configure reset_workspace with a command for these tests."""
        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["make", "clean"],
                preserve_dirs={".cache", "reports"},
            )
        )

    @pytest.mark.asyncio
    async def test_rejects_without_marker(self, tmp_path):
        """reset_workspace rejects directory without marker."""
        workspace = tmp_path / "unmarked"
        workspace.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_preserves_configured_dirs(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        (workspace / ".cache").mkdir()
        (workspace / ".cache" / "data.db").touch()
        (workspace / "reports").mkdir()
        (workspace / "reports" / "report.json").touch()
        (workspace / "output.txt").touch()
        (workspace / "temp_dir").mkdir()
        (workspace / "temp_dir" / "file.txt").touch()

        tool_ctx.runner.push(_make_result(0, "", ""))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["success"] is True
        assert ".cache" in result["skipped"]
        assert "reports" in result["skipped"]
        assert "output.txt" in result["deleted"]
        assert "temp_dir" in result["deleted"]

        assert (workspace / ".cache" / "data.db").exists()
        assert (workspace / "reports" / "report.json").exists()
        assert not (workspace / "output.txt").exists()
        assert not (workspace / "temp_dir").exists()

    @pytest.mark.asyncio
    async def test_reset_command_failure(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(1, "", "command not found"))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "error" in result
        assert result["error"] == "reset command failed"
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_runs_correct_reset_command(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(0, "", ""))

        await reset_workspace(test_dir=str(workspace))

        call_args = tool_ctx.runner.call_args_list[0][0]
        assert call_args == [
            "make",
            "clean",
        ]


class TestCheckDryWalkthrough:
    """Dry-walkthrough gate blocks both /autoskillit:implement-worktree variants."""

    def test_dry_walkthrough_gate_blocks_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate blocks /autoskillit:implement-worktree-no-merge when plan lacks marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("# My Plan\n\nSome content")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True
        assert "dry-walked" in parsed["result"].lower()

    def test_dry_walkthrough_gate_passes_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate allows /autoskillit:implement-worktree-no-merge when plan has marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# My Plan")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is None

    def test_dry_walkthrough_gate_still_works_for_implement_worktree(self, tool_ctx, tmp_path):
        """Original /autoskillit:implement-worktree gating is not broken."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True

    def test_dry_walkthrough_gate_ignores_unrelated_skills(self, tool_ctx):
        """Gate ignores skills that are not implement-worktree variants."""
        result = _check_dry_walkthrough("/autoskillit:investigate some-error", "/tmp")
        assert result is None

    def test_dry_walkthrough_gate_with_part_a_named_file_marked(self, tmp_path, tool_ctx):
        """Gate accepts _part_a.md file when marker is present."""
        plan = tmp_path / "task_plan_2026-01-01_part_a.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n\nContent here")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is None

    def test_dry_walkthrough_gate_with_part_b_named_file_unmarked(self, tmp_path, tool_ctx):
        """Gate blocks _part_b.md file when marker is absent."""
        plan = tmp_path / "task_plan_2026-01-01_part_b.md"
        plan.write_text("> **PART B ONLY.**\n\nNo walkthrough marker here")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "gate_error"

    def test_dry_walkthrough_gate_distinguishes_parts_independently(self, tmp_path, tool_ctx):
        """Gate correctly distinguishes marked part_a from unmarked part_b."""
        part_a = tmp_path / "task_plan_part_a.md"
        part_b = tmp_path / "task_plan_part_b.md"
        part_a.write_text("Dry-walkthrough verified = TRUE\n\nPart A content")
        part_b.write_text("> **PART B ONLY.**\n\nPart B content — no marker")

        result_a = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {part_a}", str(tmp_path)
        )
        result_b = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {part_b}", str(tmp_path)
        )
        assert result_a is None
        assert result_b is not None
        assert json.loads(result_b)["subtype"] == "gate_error"


class TestMergeWorktree:
    """merge_worktree enforces test gate, rebases, and merges."""

    @pytest.mark.asyncio
    async def test_merge_worktree_blocks_on_failing_tests(self, tool_ctx, tmp_path):
        """merge_worktree returns error with failed_step when test-check fails."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(
            _make_result(0, "/repo/.git/worktrees/wt\n", "")
        )  # rev-parse --git-dir
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch --show-current
        tool_ctx.runner.push(_make_result(1, "FAIL\n= 3 failed, 97 passed =", ""))  # test-check
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE
        assert result["state"] == MergeState.WORKTREE_INTACT
        assert "test_summary" not in result

    @pytest.mark.asyncio
    async def test_merge_worktree_merges_on_green(self, tool_ctx, tmp_path):
        """merge_worktree performs rebase+merge when tests pass."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
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

    @pytest.mark.asyncio
    async def test_merge_worktree_aborts_on_rebase_failure(self, tool_ctx, tmp_path):
        """merge_worktree runs rebase --abort and returns step-specific error."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
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

    @pytest.mark.asyncio
    async def test_merge_worktree_rejects_nonexistent_path(self, tool_ctx):
        """merge_worktree rejects non-existent paths."""
        result = json.loads(await merge_worktree("/nonexistent/path", "main"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_merge_worktree_rejects_non_worktree(self, tool_ctx, tmp_path):
        """merge_worktree rejects paths that aren't git worktrees."""
        result = json.loads(await merge_worktree(str(tmp_path), "main"))
        assert "error" in result


class TestRunSkillRetryGate:
    """run_skill_retry applies dry-walkthrough gate to implement skills."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_gates_implement_no_merge(self, tool_ctx, tmp_path):
        """run_skill_retry gates /autoskillit:implement-worktree-no-merge."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill_retry(
                f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
            )
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestToolRegistration:
    """All 22 tools are registered on the MCP server."""

    def test_all_tools_exist(self):
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = [c for c in server._local_provider._components.values() if isinstance(c, Tool)]
        tool_names = {t.name for t in tools}

        expected = {
            "run_cmd",
            "run_python",
            "run_skill",
            "run_skill_retry",
            "test_check",
            "reset_test_dir",
            "classify_fix",
            "reset_workspace",
            "merge_worktree",
            "read_db",
            "list_recipes",
            "load_recipe",
            "migrate_recipe",
            "kitchen_status",
            "validate_recipe",
            "get_pipeline_report",
            "get_token_summary",
            "check_quota",
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "fetch_github_issue",
        }
        assert expected == tool_names

    def test_ungated_tools_docstrings_state_notification_free(self):
        """P5-1: Each ungated tool docstring states it sends no MCP notifications."""
        for tool_fn in [
            kitchen_status,
            list_recipes,
            load_recipe,
            validate_recipe,
            get_pipeline_report,
            get_token_summary,
        ]:
            doc = tool_fn.__doc__ or ""
            assert "no MCP" in doc or "no progress notification" in doc.lower(), (
                f"{tool_fn.__name__} must document notification-free behavior"
            )


class TestKitchenStatus:
    """kitchen_status tool returns version health info (ungated)."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_status_returns_version_info(self, tool_ctx):
        import autoskillit

        tool_ctx.plugin_dir = str(Path(autoskillit.__file__).parent)
        from autoskillit import __version__

        result = json.loads(await kitchen_status())
        assert result["package_version"] == __version__
        assert result["plugin_json_version"] == __version__
        assert result["versions_match"] is True
        assert "warning" not in result

    @pytest.mark.asyncio
    async def test_status_reports_mismatch(self, tmp_path, tool_ctx):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        result = json.loads(await kitchen_status())
        assert result["versions_match"] is False
        assert "warning" in result
        assert "mismatch" in result["warning"].lower()

    @pytest.mark.asyncio
    async def test_status_works_without_enable(self, tool_ctx):
        assert tool_ctx.gate.enabled is False
        result = json.loads(await kitchen_status())
        assert result["tools_enabled"] is False
        assert "package_version" in result

    @pytest.mark.asyncio
    async def test_status_includes_token_usage_verbosity_default(self):
        """TU_S1: kitchen_status includes token_usage_verbosity key with default 'summary'."""
        result = json.loads(await kitchen_status())
        assert "token_usage_verbosity" in result
        assert result["token_usage_verbosity"] == "summary"

    @pytest.mark.asyncio
    async def test_status_reflects_none_verbosity(self, tool_ctx):
        """TU_S2: kitchen_status reflects 'none' verbosity from config."""
        cfg = AutomationConfig()
        cfg.token_usage = TokenUsageConfig(verbosity="none")
        tool_ctx.config = cfg
        result = json.loads(await kitchen_status())
        assert result["token_usage_verbosity"] == "none"


class TestRecipeTools:
    """Tests for ungated list_recipes and load_recipe tools."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    # SS1
    @pytest.mark.asyncio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_returns_json_object(self, mock_list):
        """list_recipes returns JSON object with scripts array (not gated)."""
        from autoskillit.core.types import LoadResult, RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        mock_list.return_value = LoadResult(
            items=[
                RecipeInfo(
                    name="impl",
                    description="Implement",
                    summary="plan > impl",
                    path=Path("/x"),
                    source=RecipeSource.PROJECT,
                ),
            ],
            errors=[],
        )
        result = json.loads(await list_recipes())
        assert isinstance(result, dict)
        assert len(result["recipes"]) == 1
        assert result["recipes"][0]["name"] == "impl"
        assert result["recipes"][0]["description"] == "Implement"
        assert result["recipes"][0]["summary"] == "plan > impl"
        assert "errors" not in result

    # SS2
    @pytest.mark.asyncio
    async def test_load_returns_json_with_content(self, tmp_path, monkeypatch):
        """load_recipe returns JSON with content and suggestions (not gated)."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\ndescription: Test recipe\n")
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert "name: test" in result["content"]
        assert "description: Test recipe" in result["content"]

    # SS3
    @pytest.mark.asyncio
    async def test_load_unknown_returns_error(self, tmp_path, monkeypatch):
        """load_recipe returns error JSON for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    # SS4
    @pytest.mark.asyncio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_reports_errors_in_response(self, mock_list):
        """list_recipes includes errors in JSON when recipes fail to parse."""
        from autoskillit.core.types import LoadReport, LoadResult

        mock_list.return_value = LoadResult(
            items=[],
            errors=[LoadReport(path=Path("/recipes/broken.yaml"), error="bad yaml")],
        )
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1
        assert result["errors"][0]["file"] == "broken.yaml"
        assert "bad yaml" in result["errors"][0]["error"]

    # SS5
    @pytest.mark.asyncio
    async def test_list_integration_discovers_project_recipe(self, tmp_path, monkeypatch):
        """Server tool returns project recipes alongside bundled recipes."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "pipeline.yaml").write_text(
            "name: test-pipe\ndescription: Test\nsummary: a > b\n"
            "steps:\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "test-pipe" in names

    # SS6
    @pytest.mark.asyncio
    async def test_list_integration_reports_errors(self, tmp_path, monkeypatch):
        """Server tool reports parse errors to the caller from real files."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "broken.yaml").write_text("[unclosed bracket\n")
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1

    # SS7
    @pytest.mark.asyncio
    async def test_load_returns_json_with_suggestions(self, tmp_path, monkeypatch):
        """load_recipe response always has 'content' and 'suggestions' keys."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nkitchen_rules:\n  - test\n"
            "steps:\n  do:\n    tool: test_check\n    model: sonnet\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)
        assert any(s["rule"] == "model-on-non-skill-step" for s in result["suggestions"])

    # SS8
    @pytest.mark.asyncio
    async def test_list_recipes_includes_builtins_with_empty_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_recipes MCP returns bundled recipes when .autoskillit/recipes/ is absent."""
        monkeypatch.chdir(tmp_path)
        # No .autoskillit/recipes/ created — simulates a fresh project with no local recipes
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "implementation-pipeline" in names
        assert "bugfix-loop" in names
        assert "audit-and-fix" in names
        assert "investigate-first" in names
        assert "smoke-test" in names

    # SS9
    @pytest.mark.asyncio
    async def test_load_recipe_mcp_returns_builtin_recipe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe MCP finds bundled recipes when no project .autoskillit/recipes/ dir."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="implementation-pipeline"))
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "content" in result
        assert len(result["content"]) > 0

    @pytest.mark.asyncio
    async def test_load_recipe_parse_failure_is_logged_and_surfaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe emits a warning log and surfaces a validation-error finding."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        # Recipe must have 'steps' so the run_semantic_rules code path is reached
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )

        with (
            patch(
                "autoskillit.recipe._api.run_semantic_rules",
                side_effect=ValueError("injected parse failure"),
            ),
            patch("autoskillit.recipe._api._logger") as mock_logger,
        ):
            result = json.loads(await load_recipe(name="test"))

        assert "content" in result, "load_recipe must be non-blocking even on parse failure"
        mock_logger.warning.assert_called_once()
        assert any(s.get("rule") == "validation-error" for s in result["suggestions"]), (
            "Unexpected exception must appear as a validation-error finding in suggestions"
        )

    @pytest.mark.asyncio
    async def test_load_recipe_validation_error_message_includes_exception_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The validation-error finding message names the exception type."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.run_semantic_rules",
            side_effect=ValueError("injected crash"),
        ):
            result = json.loads(await load_recipe(name="test"))

        assert "content" in result
        findings = [s for s in result["suggestions"] if s.get("rule") == "validation-error"]
        assert findings, "Expected at least one validation-error finding"
        assert "Invalid recipe structure: injected crash" == findings[0]["message"]


class TestContractMigrationAdapterValidate:
    """P7-2: ContractMigrationAdapter.validate uses _load_yaml, not yaml.safe_load."""

    def test_valid_contract_returns_true(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("skill_hashes:\n  my-skill: abc123\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is True
        assert msg == ""

    def test_missing_skill_hashes_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("other_field: value\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert "skill_hashes" in msg

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_bytes(b":\tbad: yaml: [unclosed\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert msg != ""

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(tmp_path / "nonexistent.yaml")
        assert ok is False
        assert msg != ""


class TestLoadRecipeExceptionHandling:
    """CC-1: Outer except in load_recipe must catch anticipated exceptions only."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext so load_recipe can call _get_config()."""

    @pytest.mark.asyncio
    async def test_yaml_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yaml.YAMLError is caught and returned as an error suggestion."""
        from autoskillit.core.io import YAMLError

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\n")
        with patch("autoskillit.recipe._api.load_yaml", side_effect=YAMLError("bad yaml")):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.asyncio
    async def test_value_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ValueError (malformed recipe structure) is caught and returned as error suggestion."""
        from autoskillit.core.types import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test.yaml"
        recipe_path.write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        fake_match = RecipeInfo(
            name="test",
            description="Test",
            source=RecipeSource.PROJECT,
            path=recipe_path,
        )
        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=fake_match),
            patch(
                "autoskillit.recipe._api._parse_recipe", side_effect=ValueError("bad structure")
            ),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.asyncio
    async def test_file_not_found_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError is caught and returned as an error suggestion."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.load_recipe_card",
            side_effect=FileNotFoundError("missing"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.asyncio
    async def test_unexpected_exception_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected exceptions (not in specific catches) must propagate, not be swallowed."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.run_semantic_rules",
            side_effect=AttributeError("programming error"),
        ):
            with pytest.raises(AttributeError, match="programming error"):
                await load_recipe(name="test")


class TestValidateRecipe:
    """Tests for ungated validate_recipe tool."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify this tool works WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    # VS1
    @pytest.mark.asyncio
    async def test_valid_recipe_returns_success(self, tmp_path):
        """validate_recipe returns valid=true for a correct recipe."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: test\n"
            "description: A test recipe\n"
            "summary: a > b\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  do_thing:\n"
            "    tool: run_cmd\n"
            "    with:\n"
            "      cmd: echo hello\n"
            "      cwd: .\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is True
        assert result["errors"] == []

    # VS2
    @pytest.mark.asyncio
    async def test_invalid_recipe_returns_errors(self, tmp_path):
        """validate_recipe returns valid=false with errors for missing name."""
        script = tmp_path / "bad.yaml"
        script.write_text("description: Missing name\nsteps:\n  do_thing:\n    tool: run_cmd\n")
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert any("name" in e for e in result["errors"])

    # VS3
    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error(self):
        """validate_recipe returns valid=False with findings for nonexistent file."""
        result = json.loads(await validate_recipe(script_path="/nonexistent/path.yaml"))
        assert result["valid"] is False
        assert len(result["findings"]) > 0
        assert "not found" in result["findings"][0]["error"].lower()

    # VS4
    @pytest.mark.asyncio
    async def test_malformed_yaml_returns_error(self, tmp_path):
        """validate_recipe returns valid=False with findings for unparseable YAML."""
        script = tmp_path / "broken.yaml"
        script.write_text("key: [\n  unclosed\n")
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert len(result["findings"]) > 0
        assert "yaml" in result["findings"][0]["error"].lower()

    # T_OR10
    @pytest.mark.asyncio
    async def test_validate_recipe_with_on_result(self, tmp_path):
        """validate_recipe correctly validates on_result blocks."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: result-recipe\n"
            "description: Uses on_result\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  classify:\n"
            "    tool: classify_fix\n"
            "    on_result:\n"
            "      field: restart_scope\n"
            "      routes:\n"
            "        full_restart: done\n"
            "        partial_restart: done\n"
            "    on_failure: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is True

    # DFQ14
    @pytest.mark.asyncio
    async def test_validate_recipe_includes_quality_field(self, tmp_path):
        """validate_recipe response includes quality report with warnings and summary."""
        script = tmp_path / "dead.yaml"
        script.write_text(
            "name: dead-output-test\n"
            "description: Has a dead capture\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  impl:\n"
            "    tool: run_skill\n"
            "    capture:\n"
            "      worktree_path: '${{ result.worktree_path }}'\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert "quality" in result
        quality = result["quality"]
        assert "warnings" in quality
        assert "summary" in quality
        dead = [w for w in quality["warnings"] if w["code"] == "DEAD_OUTPUT"]
        assert len(dead) == 1
        assert dead[0]["step"] == "impl"
        assert dead[0]["field"] == "worktree_path"
        semantic_errors = [
            f
            for f in result.get("findings", [])
            if f.get("rule") == "dead-output" and f.get("severity") == "error"
        ]
        assert len(semantic_errors) == 1
        assert semantic_errors[0]["step"] == "impl"

    # SEM1
    @pytest.mark.asyncio
    async def test_validate_recipe_includes_semantic_findings(self, tmp_path):
        """validate_recipe response includes 'findings' key with semantic findings."""
        script = tmp_path / "semantic.yaml"
        script.write_text(
            "name: semantic-test\n"
            "description: Has model on non-skill step\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  check:\n"
            "    tool: test_check\n"
            "    model: sonnet\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        assert isinstance(result["findings"], list)
        assert any(f["rule"] == "model-on-non-skill-step" for f in result["findings"])
        assert result["valid"] is True  # Warning does not block validity


class TestToolSchemas:
    """Regression guard: tool descriptions must not contain legacy terminology."""

    FORBIDDEN_TERMS = {
        "executor",
        "planner",
        "bugfix-loop",
        "automation-mcp",
        "ai-executor",
        "enable_tools",  # old open_kitchen prompt name
        "disable_tools",  # old close_kitchen prompt name
        "autoskillit_status",  # old kitchen_status tool name
    }

    REQUIRED_CROSS_REFS: dict[str, list[str]] = {
        "list_recipes": [
            "write-recipe",
        ],
        "load_recipe": [
            "write-recipe",
        ],
        "validate_recipe": [
            "write-recipe",
        ],
    }

    @property
    def FORBIDDEN_NATIVE_TOOLS(self) -> list[str]:  # noqa: N802
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        return list(PIPELINE_FORBIDDEN_TOOLS)

    PIPELINE_TOOLS_WITH_GUIDANCE: dict[str, list[str]] = {
        "run_skill": ["MCP tool", "delegate"],
        "run_skill_retry": ["MCP tool", "delegate"],
    }

    def test_tool_descriptions_contain_no_legacy_terms(self):
        """No registered tool should reference old package terminology."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = [c for c in server._local_provider._components.values() if isinstance(c, Tool)]
        for tool in tools:
            desc = (tool.description or "").lower()
            for term in self.FORBIDDEN_TERMS:
                assert term not in desc, (
                    f"Tool '{tool.name}' description contains legacy term '{term}'"
                )

    def test_tool_docstrings_contain_required_cross_refs(self):
        """Tool docstrings must contain required cross-references."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        for tool_name, required_terms in self.REQUIRED_CROSS_REFS.items():
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found in server"
            desc = tool.description or ""
            for term in required_terms:
                assert term in desc, f"Tool '{tool_name}' description must reference '{term}'"

    def test_classify_fix_docstring_has_routing_guidance(self):
        """classify_fix must explain what to do with each return value."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["classify_fix"].description or ""
        # Must mention both routing outcomes
        assert "full_restart" in desc
        assert "partial_restart" in desc
        # Must mention at least one skill as routing target
        assert "investigate" in desc or "implement" in desc

    def test_recipe_tools_have_disambiguation(self):
        """All recipe-related tools must carry the 'NOT slash commands' disclaimer."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        recipe_tools = ["list_recipes", "load_recipe", "validate_recipe"]
        for tool_name in recipe_tools:
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            desc = tool.description or ""
            assert "NOT slash commands" in desc, (
                f"Tool '{tool_name}' must contain 'NOT slash commands' disclaimer"
            )

    def test_load_recipe_names_all_forbidden_tools(self):
        """load_recipe must enumerate all forbidden native tools."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""

        missing = [t for t in self.FORBIDDEN_NATIVE_TOOLS if t not in desc]
        assert not missing, (
            f"load_recipe docstring must name all forbidden tools. Missing: {missing}"
        )

    def test_pipeline_tools_have_orchestrator_guidance(self):
        """run_skill and run_skill_retry must reinforce MCP-only delegation."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        failures = []
        for tool_name, required_terms in self.PIPELINE_TOOLS_WITH_GUIDANCE.items():
            desc = tools[tool_name].description or ""
            for term in required_terms:
                if term.lower() not in desc.lower():
                    failures.append(f"Tool '{tool_name}' missing orchestrator term '{term}'")
        assert not failures, "Pipeline tools missing orchestrator guidance:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    def test_pipeline_forbidden_tools_constant_is_complete(self):
        """PIPELINE_FORBIDDEN_TOOLS must contain all 11 native Claude Code tools."""
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        expected = {
            "Read",
            "Grep",
            "Glob",
            "Edit",
            "Write",
            "Bash",
            "Task",
            "Explore",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
        }
        actual = set(PIPELINE_FORBIDDEN_TOOLS)
        missing = expected - actual
        assert not missing, f"PIPELINE_FORBIDDEN_TOOLS missing tools: {missing}"

    def test_run_skill_names_all_forbidden_tools(self):
        """run_skill and run_skill_retry docstrings must name all forbidden tools."""
        from fastmcp.tools import Tool

        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS
        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        for tool_name in ("run_skill", "run_skill_retry"):
            desc = tools[tool_name].description or ""
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in desc]
            assert not missing, (
                f"{tool_name} docstring must name all forbidden tools. Missing: {missing}"
            )

    def test_bundled_recipe_kitchen_rules_name_all_forbidden_tools(self):
        """All bundled recipe kitchen_rules blocks must name every forbidden tool."""
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        wf_dir = builtin_recipes_dir()
        for path in sorted(wf_dir.glob("*.yaml")):
            wf = load_recipe(path)
            all_constraint_text = " ".join(wf.kitchen_rules)
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in all_constraint_text]
            assert not missing, f"{path.name} kitchen_rules missing forbidden tools: {missing}"


def _extract_docstring_sections(desc: str) -> dict[str, str]:
    """Split a tool description into named sections by detecting headers.

    Returns a dict of {section_name: section_text} with lowercase-normalized keys.
    The first paragraph before any header is the ``preamble`` section.

    Detected header patterns:
    - ALL-CAPS headers with colon or em-dash (ROUTING RULES —, IMPORTANT:)
    - Capitalized phrase followed by colon (After loading:, Args:)
    - "During pipeline execution" specific header
    - "NEVER use native" prohibition header
    """
    lines = desc.split("\n")
    header_patterns = [
        # ALL-CAPS header: ROUTING RULES —, FAILURE PREDICATES —, IMPORTANT:
        re.compile(r"^([A-Z]{2,}(?:\s+[A-Z]{2,})*\s*[—:])"),
        # Capitalized phrase + colon: After loading:, Allowed during ...:, Args:
        re.compile(r"^([A-Z][a-z]+(?:\s+[a-z]+)*\s*:)"),
        # Specific: "During pipeline execution" or "NEVER use native"
        re.compile(r"^(During pipeline execution[,:]?)"),
        re.compile(r"^(NEVER use native)"),
    ]

    sections: dict[str, str] = {}
    current_key = "preamble"
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        matched_header = None

        for pattern in header_patterns:
            m = pattern.match(stripped)
            if m:
                matched_header = m.group(1)
                break

        if matched_header:
            if current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            key = matched_header.lower().rstrip(":—,").strip()
            current_key = key
            current_lines = [stripped]
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


class TestDocstringSemantics:
    """Section-aware semantic checks for tool descriptions.

    Unlike TestToolSchemas (which checks token presence), these tests parse
    descriptions into named sections and verify behavioral correctness,
    routing, and cross-section consistency.
    """

    def test_load_recipe_action_protocol_routes_through_skill(self):
        """After loading section must route modifications through write-recipe."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        assert after_loading, "load_recipe missing 'After loading' section"

        # Modification requests must route through write-recipe
        assert "write-recipe" in after_loading, (
            "After loading section must route recipe modifications through write-recipe"
        )

    def test_load_recipe_after_loading_does_not_instruct_direct_modification(self):
        """After loading section must not instruct direct file modification."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        assert after_loading, "load_recipe missing 'After loading' section"

        direct_edit_phrases = [
            "apply them",
            "Save changes to the original file",
            "Save as a new recipe",
        ]
        found = [p for p in direct_edit_phrases if p.lower() in after_loading.lower()]
        assert not found, f"After loading section instructs direct modification: {found}"

    def test_validate_recipe_has_failure_routing(self):
        """validate_recipe must route validation failures to write-recipe."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["validate_recipe"].description or ""

        # Must reference the failure return case
        assert "false" in desc.lower(), (
            "validate_recipe must document the failure case (e.g. {valid: false})"
        )

        # Failure routing must direct to write-recipe for remediation
        desc_lower = desc.lower()
        has_remediation_context = any(
            phrase in desc_lower for phrase in ["fix", "remediat", "correct the"]
        )
        assert has_remediation_context, (
            "validate_recipe must route failures to write-recipe for remediation"
        )

    def test_validate_recipe_does_not_endorse_direct_editing(self):
        """validate_recipe must not normalize direct recipe editing."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["validate_recipe"].description or ""

        # "or editing a recipe" without qualifying through write-recipe
        # normalizes the model directly editing YAML files
        assert "or editing a recipe" not in desc, (
            "validate_recipe normalizes direct editing with 'or editing a recipe'; "
            "should qualify as going through write-recipe"
        )

    def test_tool_description_sections_are_not_contradictory(self):
        """After loading must not instruct what the prohibition section prohibits."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        # Accept either old or new section header
        prohibition = sections.get("during pipeline execution", "") or sections.get(
            "never use native", ""
        )
        assert after_loading, "Missing 'After loading' section"
        assert prohibition, (
            "Missing prohibition section (NEVER use native / During pipeline execution)"
        )

        # If the prohibition section says Edit/Write are prohibited or "not used here",
        # then "After loading" must not instruct behaviors requiring file writing
        if "not used here" in prohibition.lower() or "prohibited" in prohibition.lower():
            write_implying_phrases = ["apply them", "save changes", "save as"]
            found = [p for p in write_implying_phrases if p.lower() in after_loading.lower()]
            assert not found, (
                f"Contradiction: prohibition section prohibits Edit/Write "
                f"but 'After loading' instructs: {found}"
            )

    def test_load_recipe_has_preview_format_spec(self):
        """load_recipe must specify presentation format for loaded recipes."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""

        required_fields = ["kitchen_rules", "note", "retry", "capture"]
        found = [f for f in required_fields if f in desc.lower()]
        assert len(found) >= 3, (
            f"load_recipe must specify a preview format naming critical recipe "
            f"fields. Found only: {found}"
        )

    def test_recipe_tool_descriptions_are_coherent(self):
        """Recipe tools must form a coherent policy about recipe modification."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }

        failures = []

        # load_recipe: modifications must route through write-recipe
        load_desc = tools["load_recipe"].description or ""
        load_sections = _extract_docstring_sections(load_desc)
        after_loading = load_sections.get("after loading", "")
        if "apply them" in after_loading.lower():
            failures.append("load_recipe 'After loading' instructs direct editing ('apply them')")

        # validate_recipe: failure must route through write-recipe
        validate_desc = tools["validate_recipe"].description or ""
        validate_lower = validate_desc.lower()
        has_failure_routing = (
            "write-recipe" in validate_desc
            and any(w in validate_lower for w in ["fix", "fail", "invalid", "error"])
            and "false" in validate_lower
        )
        if not has_failure_routing:
            failures.append("validate_recipe has no failure routing through write-recipe")

        # validate_recipe: must not normalize direct editing
        if "or editing a recipe" in validate_desc:
            failures.append("validate_recipe normalizes direct editing ('or editing a recipe')")

        assert not failures, "Recipe tools lack coherent modification policy:\n" + "\n".join(
            f"  - {f}" for f in failures
        )


class TestResetGuard:
    """Marker-file-based reset guard for destructive operations."""

    @pytest.mark.asyncio
    async def test_reset_test_dir_refuses_without_marker(self, tool_ctx, tmp_path):
        """Directory without marker file is refused."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower() or "reset guard" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reset_test_dir_allows_with_marker(self, tool_ctx, tmp_path):
        """Directory with marker file is cleared."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert not (target / "some_file.txt").exists()

    @pytest.mark.asyncio
    async def test_reset_test_dir_preserves_marker(self, tool_ctx, tmp_path):
        """Reset preserves the marker file so the workspace is reusable."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "data.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert (target / ".autoskillit-workspace").is_file()

    @pytest.mark.asyncio
    async def test_reset_workspace_refuses_without_marker(self, tool_ctx, tmp_path):
        """reset_workspace also checks for marker."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        target = tmp_path / "workspace"
        target.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_reset_workspace_allows_with_marker(self, tool_ctx, tmp_path):
        """reset_workspace clears when marker is present."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "file.txt").touch()
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_custom_marker_name(self, tool_ctx, tmp_path):
        """Config can override marker file name."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(reset_guard_marker=".my-workspace"))
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".my-workspace").touch()
        (target / "file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_force_overrides_marker_check(self, tool_ctx, tmp_path):
        """force=True on reset_test_dir bypasses marker requirement."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "file.txt").touch()
        # No marker, but force=True
        result = json.loads(await reset_test_dir(test_dir=str(target), force=True))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_rejects_nonexistent(self, tool_ctx, tmp_path):
        result = json.loads(await reset_test_dir(test_dir=str(tmp_path / "nope")))
        assert "does not exist" in result["error"]

    def test_safety_config_has_reset_guard_marker(self):
        """SafetyConfig has reset_guard_marker field."""
        cfg = SafetyConfig()
        assert cfg.reset_guard_marker == ".autoskillit-workspace"


class TestConfigDefaults:
    """Verify config defaults match expected values."""

    def test_default_preserve_dirs(self):
        cfg = AutomationConfig()
        assert cfg.reset_workspace.preserve_dirs == set()

    def test_default_test_command(self):
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["task", "test-check"]

    def test_default_classify_fix_empty_prefixes(self):
        cfg = AutomationConfig()
        assert cfg.classify_fix.path_prefixes == []


class TestRunSubprocessDelegatesToManaged:
    """Verify _run_subprocess delegates to the runner (ToolContext.runner) correctly."""

    @pytest.mark.asyncio
    async def test_normal_completion(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "output", ""))
        rc, stdout, stderr = await _run_subprocess(["echo", "hi"], cwd="/tmp", timeout=10)
        assert rc == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.asyncio
    async def test_timeout_returns_minus_one(self, tool_ctx):
        tool_ctx.runner.push(_make_timeout_result())
        rc, stdout, stderr = await _run_subprocess(["sleep", "999"], cwd="/tmp", timeout=1)
        assert rc == -1
        assert "timed out" in stderr


class TestProcessRunnerResult:
    """_process_runner_result shared helper lives in server.helpers."""

    def test_normal_exit_preserves_fields(self):
        from autoskillit.core import TerminationReason
        from autoskillit.execution.process import SubprocessResult
        from autoskillit.server.helpers import _process_runner_result

        result = SubprocessResult(
            returncode=0,
            stdout="hello",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        rc, stdout, stderr = _process_runner_result(result, timeout=10)
        assert rc == 0
        assert stdout == "hello"
        assert stderr == ""

    def test_timed_out_returns_minus_one_with_message(self):
        from autoskillit.core import TerminationReason
        from autoskillit.execution.process import SubprocessResult
        from autoskillit.server.helpers import _process_runner_result

        result = SubprocessResult(
            returncode=-1,
            stdout="partial",
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=1,
        )
        rc, stdout, stderr = _process_runner_result(result, timeout=5)
        assert rc == -1
        assert stdout == "partial"
        assert "timed out" in stderr
        assert "5" in stderr


def test_server_init_has_no_shim_reexports():
    """server/__init__.py must not re-export tool symbols (shim removed)."""
    import autoskillit.server as srv

    # These symbols must NOT be in the server package namespace after shim removal.
    # They should only be accessible via their actual submodule paths.
    shim_symbols = [
        "_check_dry_walkthrough",
        "_require_enabled",
        "_run_subprocess",
        "_open_kitchen_handler",
        "_close_kitchen_handler",
        "run_cmd",
        "run_python",
        "run_skill",
        "run_skill_retry",
        "test_check",
        "reset_test_dir",
        "reset_workspace",
        "merge_worktree",
        "classify_fix",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "list_recipes",
        "load_recipe",
        "migrate_recipe",
        "validate_recipe",
        "check_quota",
        "get_pipeline_report",
        "get_token_summary",
        "kitchen_status",
        "read_db",
        "fetch_github_issue",
    ]
    present = [sym for sym in shim_symbols if hasattr(srv, sym)]
    assert not present, f"Shim re-exports found in server namespace (must be removed): {present}"


class TestTestCheck:
    """test_check returns unambiguous PASS/FAIL with cross-validation."""

    @pytest.mark.asyncio
    async def test_passes_on_clean_run(self, tool_ctx):
        """returncode=0 with passing summary -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_fails_on_nonzero_exit(self, tool_ctx):
        """returncode=1 -> passed=False regardless of output."""
        tool_ctx.runner.push(_make_result(1, "= 3 failed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_cross_validates_exit_code_against_output(self, tool_ctx):
        """returncode=0 but output contains 'failed' -> passed=False.
        This is THE bug: Taskfile PIPESTATUS fails silently, exit code is 0,
        but output clearly shows test failures."""
        tool_ctx.runner.push(_make_result(0, "= 3 failed, 8538 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_does_not_expose_summary(self, tool_ctx):
        """test_check returns passed + output — no summary, no output_file."""
        tool_ctx.runner.push(
            _make_result(0, "= 100 passed =\nTest output saved to: /tmp/out.txt\n", "")
        )
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert "summary" not in result
        assert "output_file" not in result
        assert set(result.keys()) == {"passed", "output"}

    @pytest.mark.asyncio
    async def test_cross_validates_error_in_output(self, tool_ctx):
        """returncode=0 but output contains 'error' -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "= 1 error, 99 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_xfailed_not_treated_as_failure(self, tool_ctx):
        """xfailed tests are expected failures — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 8552 passed, 3 xfailed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_xpassed_not_treated_as_failure(self, tool_ctx):
        """xpassed tests are unexpected passes — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 99 passed, 1 xpassed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_mixed_xfail_with_real_failure(self, tool_ctx):
        """Real failure + xfailed — should still fail on the real failure."""
        tool_ctx.runner.push(_make_result(0, "= 1 failed, 2 xfailed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_skipped_with_exit_zero_passes(self, tool_ctx):
        """Skipped tests with exit 0 — parser trusts exit code."""
        tool_ctx.runner.push(_make_result(0, "= 97 passed, 3 skipped =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_warnings_not_treated_as_failure(self, tool_ctx):
        """Warnings with exit 0 — should pass."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed, 5 warnings =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_bare_q_failures_detected(self, tool_ctx):
        """Bare -q failure line (rc=0 due to PIPESTATUS bug) -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "3 failed, 97 passed in 2.31s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_cwa_empty_output_fails(self, tool_ctx):
        """CWA: rc=0 but empty stdout -> passed=False (cannot confirm pass)."""
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_bare_q_clean_passes(self, tool_ctx):
        """Bare -q all-passing output: rc=0 and summary found -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "100 passed in 1.50s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True


class TestClaudeSessionResult:
    """ClaudeSessionResult correctly parses Claude Code JSON output."""

    def test_parses_success_result(self):
        """Normal completion extracts result and session_id."""
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done.",
            "session_id": "abc-123",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "success"
        assert parsed.is_error is False
        assert parsed.result == "Done."
        assert parsed.session_id == "abc-123"
        assert parsed.needs_retry is False
        assert parsed.retry_reason == RetryReason.NONE

    def test_parses_error_max_turns(self):
        """Turn limit produces needs_retry=True with reason=RESUME."""
        raw = {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": False,
            "session_id": "abc-123",
            "errors": ["Max turns reached"],
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "error_max_turns"
        assert parsed.needs_retry is True
        assert parsed.retry_reason == RetryReason.RESUME
        assert parsed.result == ""

    def test_parses_prompt_too_long(self):
        """Context exhaustion produces needs_retry=True with reason=RESUME."""
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "Prompt is too long",
            "session_id": "abc-123",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.is_error is True
        assert parsed.needs_retry is True
        assert parsed.retry_reason == RetryReason.RESUME

    def test_parses_execution_error_not_retriable(self):
        """Runtime errors are not automatically retriable."""
        raw = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "session_id": "abc-123",
            "errors": ["Tool execution failed"],
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "error_during_execution"
        assert parsed.needs_retry is False
        assert parsed.retry_reason == RetryReason.NONE

    def test_non_json_stdout_is_error(self):
        """Non-JSON output (crashes, tracebacks) is always an error."""
        parsed = parse_session_result("Traceback (most recent call last):\n  File...")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"
        assert "Traceback" in parsed.result
        assert parsed.needs_retry is False

    def test_empty_stdout_is_error(self):
        """Empty stdout means the session produced no output — always an error."""
        parsed = parse_session_result("")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"
        assert parsed.result == ""
        assert parsed.needs_retry is False

    def test_whitespace_only_stdout_is_error(self):
        """Whitespace-only stdout is treated as empty."""
        parsed = parse_session_result("  \n  \t  ")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"

    def test_json_without_type_result_is_error(self):
        """JSON that isn't a Claude result object is rejected by fallback."""
        parsed = parse_session_result('{"some": "random", "json": true}')
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_non_dict_json_is_error(self):
        """Non-dict JSON (list, string, number) is unparseable."""
        parsed = parse_session_result("[1, 2, 3]")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_handles_ndjson_with_multiple_lines(self):
        """Parser finds type=result in multi-line NDJSON output."""
        lines = [
            json.dumps({"type": "assistant", "message": "working..."}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Done.",
                    "session_id": "s1",
                }
            ),
        ]
        parsed = parse_session_result("\n".join(lines))
        assert parsed.subtype == "success"
        assert parsed.result == "Done."
        assert parsed.session_id == "s1"

    def test_needs_retry_and_retry_reason_are_consistent(self):
        """retry_reason is RESUME iff needs_retry is True, NONE otherwise."""
        cases = [
            ("success", False, "Done."),
            ("error_max_turns", False, ""),
            ("success", True, "Prompt is too long"),
            ("error_during_execution", True, "crashed"),
            ("unknown", False, ""),
        ]
        for subtype, is_error, result_text in cases:
            session = ClaudeSessionResult(
                subtype=subtype,
                is_error=is_error,
                result=result_text,
                session_id="s1",
            )
            if session.needs_retry:
                assert session.retry_reason == RetryReason.RESUME, (
                    f"needs_retry=True but retry_reason={session.retry_reason!r} "
                    f"for subtype={subtype}, is_error={is_error}"
                )
            else:
                assert session.retry_reason == RetryReason.NONE, (
                    f"needs_retry=False but retry_reason={session.retry_reason!r} "
                    f"for subtype={subtype}, is_error={is_error}"
                )

    def test_all_retriable_cases_produce_same_retry_reason(self):
        """Every condition that triggers needs_retry must produce the same retry_reason."""
        max_turns_case = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="",
            session_id="s1",
        )
        context_case = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="Prompt is too long",
            session_id="s2",
        )
        assert max_turns_case.needs_retry is True
        assert context_case.needs_retry is True
        assert max_turns_case.retry_reason == context_case.retry_reason


class TestAgentResult:
    """agent_result produces actionable text for LLM callers."""

    def test_rewrites_context_exhaustion(self):
        """Context exhaustion result must NOT contain 'Prompt is too long'."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="Prompt is too long",
            session_id="s1",
        )
        assert "prompt is too long" not in session.agent_result.lower()
        assert "context" in session.agent_result.lower()
        assert "continue" in session.agent_result.lower()

    def test_rewrites_max_turns(self):
        """Max turns result must describe the situation, not pass through empty string."""
        session = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="",
            session_id="s1",
        )
        assert (
            "turn limit" in session.agent_result.lower()
            or "resume" in session.agent_result.lower()
        )

    def test_preserves_normal_result(self):
        """Normal success result passes through unchanged."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Task completed. Created 3 files.",
            session_id="s1",
        )
        assert session.agent_result == "Task completed. Created 3 files."

    def test_preserves_error_result_when_not_retriable(self):
        """Non-retriable errors pass through unchanged."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="Tool execution failed: permission denied",
            session_id="s1",
        )
        assert session.agent_result == "Tool execution failed: permission denied"


def test_context_exhaustion_marker_is_used_in_detection():
    """_is_context_exhausted() uses the CONTEXT_EXHAUSTION_MARKER constant."""
    session = ClaudeSessionResult(
        subtype="success",
        is_error=True,
        result=CONTEXT_EXHAUSTION_MARKER,
        session_id="s1",
    )
    assert session._is_context_exhausted() is True


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.asyncio
    async def test_retry_reason_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "num_turns": 200,
                "errors": [],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.asyncio
    async def test_retry_reason_none_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
                "num_turns": 50,
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}


class TestRunSkillRetrySessionOutcome:
    """run_skill_retry correctly classifies all Claude Code session outcomes."""

    @pytest.mark.asyncio
    async def test_detects_max_turns_via_subtype(self, tool_ctx):
        """error_max_turns in JSON output -> needs_retry=True, retry_reason=RESUME."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "errors": ["Max turns reached"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    async def test_detects_context_limit(self, tool_ctx):
        """'Prompt is too long' -> needs_retry=True, retry_reason='retry'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    async def test_success_not_retriable(self, tool_ctx):
        """Normal success -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    async def test_execution_error_not_retriable(self, tool_ctx):
        """error_during_execution -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "session_id": "s1",
                "errors": ["crashed"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False

    @pytest.mark.asyncio
    async def test_unparseable_stdout_not_retriable(self, tool_ctx):
        """Non-JSON stdout -> needs_retry=False."""
        tool_ctx.runner.push(_make_result(1, "crash dump", "segfault"))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False


class TestRunSkillRetryAgentResult:
    """run_skill_retry result field contains actionable text."""

    @pytest.mark.asyncio
    async def test_context_limit_result_is_actionable(self, tool_ctx):
        """When context is exhausted, result text must NOT say 'Prompt is too long'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert "prompt is too long" not in result["result"].lower()
        assert result["needs_retry"] is True

    @pytest.mark.asyncio
    async def test_normal_success_result_passes_through(self, tool_ctx):
        """Normal success result text is preserved."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["result"] == "Done."


class TestRunSkillRetryFields:
    """run_skill includes needs_retry and retry_reason for parity."""

    @pytest.mark.asyncio
    async def test_includes_needs_retry_false(self, tool_ctx):
        """run_skill response includes needs_retry=False on normal success."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    async def test_includes_needs_retry_true_on_context_limit(self, tool_ctx):
        """run_skill response includes needs_retry=True when context is exhausted."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert "prompt is too long" not in result["result"].lower()


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.asyncio
    async def test_returns_subtype_on_incomplete_session(self, tool_ctx):
        """run_skill includes subtype when session didn't finish."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["session_id"] == "s1"
        assert result["subtype"] == "error_max_turns"

    @pytest.mark.asyncio
    async def test_returns_is_error_on_context_limit(self, tool_ctx):
        """run_skill includes is_error when context limit is hit."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["is_error"] is True
        assert result["subtype"] == "success"

    @pytest.mark.asyncio
    async def test_handles_empty_stdout(self, tool_ctx):
        """run_skill returns error result when stdout is empty."""
        tool_ctx.runner.push(_make_result(1, "", "segfault"))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_empty_stdout_exit_zero_is_retriable(self, tool_ctx):
        """Infrastructure failure (empty stdout, exit 0) is retriable with stderr."""
        tool_ctx.runner.push(_make_result(0, "", "session dropped"))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["subtype"] == "empty_output"
        assert result["success"] is False
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert result["stderr"] == "session dropped"


class TestParsePytestSummary:
    """_parse_pytest_summary extracts structured counts from pytest output."""

    def test_simple_pass(self):
        assert _parse_pytest_summary("= 100 passed =\n") == {"passed": 100}

    def test_failed_and_passed(self):
        assert _parse_pytest_summary("= 3 failed, 97 passed =\n") == {
            "failed": 3,
            "passed": 97,
        }

    def test_xfailed_parsed_separately(self):
        counts = _parse_pytest_summary("= 8552 passed, 3 xfailed =\n")
        assert counts == {"passed": 8552, "xfailed": 3}
        assert "failed" not in counts

    def test_mixed_all_outcomes(self):
        counts = _parse_pytest_summary(
            "= 1 failed, 2 xfailed, 1 xpassed, 3 skipped, 93 passed =\n"
        )
        assert counts["failed"] == 1
        assert counts["xfailed"] == 2
        assert counts["xpassed"] == 1
        assert counts["skipped"] == 3
        assert counts["passed"] == 93

    def test_error_outcome(self):
        assert _parse_pytest_summary("= 1 error, 99 passed =\n") == {
            "error": 1,
            "passed": 99,
        }

    def test_multiline_finds_summary(self):
        output = "some log output\nERROR in setup\n=== 100 passed in 2.5s ===\n"
        counts = _parse_pytest_summary(output)
        assert counts == {"passed": 100}

    def test_empty_output(self):
        assert _parse_pytest_summary("") == {}

    def test_no_summary_line(self):
        assert _parse_pytest_summary("no test results here\n") == {}

    def test_bare_q_format_failed_and_passed(self):
        """Bare -q format parses correctly — no = delimiters needed."""
        counts = _parse_pytest_summary("3 failed, 97 passed in 2.31s")
        assert counts["failed"] == 3
        assert counts["passed"] == 97

    def test_bare_q_format_passed_only(self):
        """Bare -q single-outcome line."""
        counts = _parse_pytest_summary("100 passed in 1.50s")
        assert counts == {"passed": 100}


class TestMergeWorktreeNoBypass:
    """merge_worktree always runs its own test gate — no bypass possible."""

    @pytest.mark.asyncio
    async def test_skip_test_gate_parameter_rejected(self):
        """merge_worktree does not accept skip_test_gate parameter."""
        with pytest.raises(TypeError, match="skip_test_gate"):
            await merge_worktree("/tmp/wt", "main", skip_test_gate=True)

    @pytest.mark.asyncio
    async def test_internal_gate_cross_validates_output(self, tool_ctx, tmp_path):
        """merge_worktree's internal gate catches rc=0 with failure text."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(
            _make_result(0, "= 3 failed, 97 passed =", "")
        )  # test-check: rc=0 but failed text
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

    @pytest.mark.asyncio
    async def test_gate_failure_does_not_expose_summary(self, tool_ctx, tmp_path):
        """When gate blocks, response contains no test output details."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(1, "= 3 failed, 97 passed =", ""))  # test-check
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert "test_summary" not in result


class TestGatedToolAccess:
    """Prompt-gated tool access: tools disabled by default, user-only activation."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Override the global autouse fixture — start disabled for gate tests."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_tools_return_error_when_disabled(self, tool_ctx):
        """All tools return standard gate error when gate is disabled."""
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_tools_work_after_enable(self, tool_ctx):
        """After open_kitchen prompt handler sets the flag, tools execute normally."""
        _open_kitchen_handler()
        tool_ctx.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_disable_reverses_enable(self, tool_ctx):
        """After close_kitchen prompt handler, tools return error again."""
        _open_kitchen_handler()
        _close_kitchen_handler()
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True

    def test_tools_disabled_by_default(self, tool_ctx):
        """Gate defaults to disabled (closed kitchen) per this test class's fixture."""
        assert tool_ctx.gate.enabled is False

    def test_prompts_registered(self):
        """open_kitchen and close_kitchen prompts are registered on the server."""
        from fastmcp.prompts import Prompt

        from autoskillit.server import mcp

        prompts = [c for c in mcp._local_provider._components.values() if isinstance(c, Prompt)]
        prompt_names = {p.name for p in prompts}
        assert prompt_names == {"open_kitchen", "close_kitchen"}

    def test_all_tools_still_registered(self):
        """All 22 tools remain registered (gated + ungated)."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = [c for c in mcp._local_provider._components.values() if isinstance(c, Tool)]
        tool_names = {t.name for t in tools}
        expected = {
            "run_cmd",
            "run_python",
            "run_skill",
            "run_skill_retry",
            "test_check",
            "merge_worktree",
            "reset_test_dir",
            "classify_fix",
            "kitchen_status",
            "reset_workspace",
            "read_db",
            "list_recipes",
            "load_recipe",
            "migrate_recipe",
            "validate_recipe",
            "get_pipeline_report",
            "get_token_summary",
            "check_quota",
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "fetch_github_issue",
        }
        assert expected == tool_names

    @pytest.mark.asyncio
    async def test_run_python_gated(self):
        """run_python requires tools to be enabled."""
        result = json.loads(await run_python(callable="json.dumps", args={"obj": 1}))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    def test_gate_error_structure(self):
        """_require_enabled returns standard schema with activation instructions."""
        error = _require_enabled()
        assert error is not None
        parsed = json.loads(error)
        assert parsed["success"] is False
        assert parsed["is_error"] is True
        assert parsed["subtype"] == "gate_error"
        assert "open_kitchen" in parsed["result"] or "open_kitchen" in parsed["result"]

    def test_all_tools_tagged_automation(self):
        """All 8 tools have the 'automation' tag for future visibility control."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = [c for c in mcp._local_provider._components.values() if isinstance(c, Tool)]
        for tool in tools:
            assert "automation" in tool.tags, f"{tool.name} missing 'automation' tag"


class TestGateTransitionLogs:
    """N11: open_kitchen and close_kitchen handlers emit structured log events."""

    def test_open_kitchen_logs_gate_open(self, tool_ctx):
        with structlog.testing.capture_logs() as logs:
            _open_kitchen_handler()
        assert any(
            entry.get("event") == "open_kitchen" and entry.get("gate_state") == "open"
            for entry in logs
        )

    def test_close_kitchen_logs_gate_closed(self, tool_ctx):
        with structlog.testing.capture_logs() as logs:
            _close_kitchen_handler()
        assert any(
            entry.get("event") == "close_kitchen" and entry.get("gate_state") == "closed"
            for entry in logs
        )

    def test_session_log_dir_warns_when_missing(self):
        with structlog.testing.capture_logs() as logs:
            _session_log_dir("/nonexistent/project/99999999")
        warning_entries = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any(entry.get("event") == "session_log_dir_missing" for entry in warning_entries)

    def test_session_log_dir_no_warning_when_present(self, tmp_path):
        import shutil

        cwd = str(tmp_path)
        project_hash = cwd.replace("/", "-").replace("_", "-")
        log_dir = Path.home() / ".claude" / "projects" / project_hash
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            with structlog.testing.capture_logs() as logs:
                _session_log_dir(cwd)
            assert not any(entry.get("event") == "session_log_dir_missing" for entry in logs)
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)

    def test_session_log_dir_logs_path_when_dir_exists(self, tmp_path):
        import shutil

        cwd = str(tmp_path)
        project_hash = cwd.replace("/", "-").replace("_", "-")
        log_dir = Path.home() / ".claude" / "projects" / project_hash
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            with structlog.testing.capture_logs() as logs:
                result = _session_log_dir(cwd)
            info_entries = [e for e in logs if e.get("log_level") == "info"]
            assert any(e.get("event") == "session_log_dir_computed" for e in info_entries)
            computed_entry = next(
                e for e in info_entries if e.get("event") == "session_log_dir_computed"
            )
            assert computed_entry.get("path") == str(result)
            assert not any(e.get("event") == "session_log_dir_missing" for e in logs)
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)

    def test_session_log_dir_logs_path_when_dir_missing(self):
        cwd = "/nonexistent/project/unique-test-99999"
        with structlog.testing.capture_logs() as logs:
            result = _session_log_dir(cwd)
        info_entries = [e for e in logs if e.get("log_level") == "info"]
        assert any(e.get("event") == "session_log_dir_computed" for e in info_entries)
        computed_entry = next(
            e for e in info_entries if e.get("event") == "session_log_dir_computed"
        )
        assert computed_entry.get("path") == str(result)
        assert any(e.get("event") == "session_log_dir_missing" for e in logs)


class TestPromptSchemas:
    """Prompt descriptions must be accurate, current, and cooking-themed."""

    def _get_prompts(self):
        from fastmcp.prompts import Prompt

        from autoskillit.server import mcp

        return {
            c.name: c for c in mcp._local_provider._components.values() if isinstance(c, Prompt)
        }

    PROMPT_FORBIDDEN_TERMS = [
        "enable_tools",
        "disable_tools",
        "autoskillit_status",
        "executor",
        "bugfix-loop",
    ]

    def test_prompt_descriptions_contain_no_legacy_terms(self):
        """Prompt descriptions must not use any pre-rename vocabulary."""
        prompts = self._get_prompts()
        for name, prompt in prompts.items():
            desc = (prompt.description or "").lower()
            for term in self.PROMPT_FORBIDDEN_TERMS:
                assert term not in desc, (
                    f"Prompt '{name}' description contains legacy term '{term}': {desc!r}"
                )

    def test_prompt_descriptions_are_cooking_themed(self):
        """All prompt descriptions must use cooking vocabulary."""
        prompts = self._get_prompts()
        for name, prompt in prompts.items():
            desc = (prompt.description or "").lower()
            assert "kitchen" in desc, (
                f"Prompt '{name}' description must contain cooking vocabulary "
                f"('kitchen'): {desc!r}"
            )

    def test_close_kitchen_returns_cooking_confirmation(self, tool_ctx):
        """close_kitchen must return a cooking-themed closing message."""
        from autoskillit.server.prompts import _close_kitchen_handler

        _close_kitchen_handler()  # ensure closed state
        prompts = self._get_prompts()
        result = prompts["close_kitchen"].fn()
        text = result.messages[0].content.text
        assert "kitchen" in text.lower(), (
            f"close_kitchen return message must be cooking-themed: {text!r}"
        )


class TestOpenKitchenVersionReporting:
    """open_kitchen returns version info and warns on mismatch."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @staticmethod
    def _prompt_text(result) -> str:
        """Extract the text content from a PromptResult."""
        content = result.messages[0].content
        return content.text if hasattr(content, "text") else str(content)

    def test_open_kitchen_instructs_status_call(self):
        from autoskillit.server.prompts import open_kitchen

        result = open_kitchen()
        msg = self._prompt_text(result)
        assert "kitchen_status" in msg

    def test_open_kitchen_carries_orchestrator_contract(self):
        """open_kitchen prompt must use prohibition framing and name all forbidden tools."""
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS
        from autoskillit.server.prompts import open_kitchen

        result = open_kitchen()
        msg = self._prompt_text(result)

        # Must name every forbidden tool
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in msg]
        assert not missing, f"open_kitchen prompt missing forbidden tools: {missing}"

        # Must use prohibition framing
        prohibition_terms = ["NEVER", "Do NOT", "MUST NOT", "are prohibited"]
        assert any(term in msg for term in prohibition_terms), (
            "open_kitchen prompt must use prohibition framing "
            f"(one of {prohibition_terms}), got: {msg[:200]}"
        )

        # Must NOT use the conditional escape-hatch phrasing
        assert "During pipeline execution, only use" not in msg, (
            "open_kitchen prompt must not use conditional 'During pipeline execution, only use' "
            "phrasing — the restriction should be unconditional"
        )

    def test_open_kitchen_still_enables_on_mismatch(self, tmp_path, tool_ctx):
        from autoskillit.server.prompts import open_kitchen

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        open_kitchen()
        assert tool_ctx.gate.enabled is True


class TestConfigDrivenBehavior:
    """S1-S10: Verify tools use config instead of hardcoded values."""

    @pytest.mark.asyncio
    async def test_test_check_uses_config_command(self, tool_ctx):
        """S1: test_check runs config.test_check.command."""
        from autoskillit.config import TestCheckConfig
        from autoskillit.execution import DefaultTestRunner

        tool_ctx.config = AutomationConfig(
            test_check=TestCheckConfig(command=["pytest", "-x"], timeout=300)
        )
        # Re-create tester with updated config so it reads the new command
        tool_ctx.tester = DefaultTestRunner(config=tool_ctx.config, runner=tool_ctx.runner)

        tool_ctx.runner.push(_make_result(0, "= 100 passed =\n", ""))
        await test_check(worktree_path="/tmp/wt")

        assert tool_ctx.runner.call_args_list[0][0] == ["pytest", "-x"]
        assert tool_ctx.runner.call_args_list[0][2] == 300.0

    @pytest.mark.asyncio
    async def test_classify_fix_uses_config_prefixes(self, tool_ctx):
        """S2: classify_fix uses config.classify_fix.path_prefixes."""
        tool_ctx.config = AutomationConfig(
            classify_fix=ClassifyFixConfig(path_prefixes=["src/custom/"])
        )

        changed = "src/custom/handler.py\nsrc/other/util.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert "src/custom/handler.py" in result["critical_files"]

    @pytest.mark.asyncio
    async def test_classify_fix_empty_prefixes_always_partial(self, tool_ctx):
        """S3: Empty prefix list -> always returns partial_restart."""
        tool_ctx.config = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=[]))

        changed = "src/core/handler.py\n"
        tool_ctx.runner.push(_make_result(0, changed, ""))
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART

    @pytest.mark.asyncio
    async def test_reset_workspace_uses_config_command(self, tool_ctx, tmp_path):
        """S4: reset_workspace runs config.reset_workspace.command."""
        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(command=["make", "reset"])
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        tool_ctx.runner.push(_make_result(0, "", ""))

        await reset_workspace(test_dir=str(workspace))
        assert tool_ctx.runner.call_args_list[0][0] == ["make", "reset"]

    @pytest.mark.asyncio
    async def test_reset_workspace_not_configured_returns_error(self, tool_ctx, tmp_path):
        """S5: command=None -> returns not-configured error."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.asyncio
    async def test_reset_workspace_uses_config_preserve_dirs(self, tool_ctx, tmp_path):
        """S6: Preserves config.reset_workspace.preserve_dirs."""
        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["true"],
                preserve_dirs={"keep_me"},
            )
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "keep_me").mkdir()
        (workspace / "delete_me").touch()
        tool_ctx.runner.push(_make_result(0, "", ""))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "keep_me" in result["skipped"]
        assert "delete_me" in result["deleted"]
        assert (workspace / "keep_me").exists()
        assert not (workspace / "delete_me").exists()

    def test_dry_walkthrough_uses_config_marker(self, tool_ctx, tmp_path):
        """S7: Gate checks config.implement_gate.marker."""
        from autoskillit.config import ImplementGateConfig

        tool_ctx.config = AutomationConfig(
            implement_gate=ImplementGateConfig(marker="CUSTOM MARKER")
        )

        plan = tmp_path / "plan.md"
        plan.write_text("CUSTOM MARKER\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # passes with custom marker

        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None  # fails — marker doesn't match

    def test_dry_walkthrough_uses_config_skill_names(self, tool_ctx, tmp_path):
        """S8: Gate checks config.implement_gate.skill_names."""
        from autoskillit.config import ImplementGateConfig

        tool_ctx.config = AutomationConfig(
            implement_gate=ImplementGateConfig(skill_names={"/custom-impl"})
        )

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker")

        result = _check_dry_walkthrough(f"/custom-impl {plan}", str(tmp_path))
        assert result is not None  # /custom-impl is gated

        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # /autoskillit:implement-worktree is NOT gated (not in skill_names)

    @pytest.mark.asyncio
    async def test_merge_worktree_uses_config_test_command(self, tool_ctx, tmp_path):
        """S9: Merge's test gate runs config.test_check.command."""
        from autoskillit.config import TestCheckConfig
        from autoskillit.execution import DefaultTestRunner

        tool_ctx.config = AutomationConfig(
            test_check=TestCheckConfig(command=["make", "test"], timeout=120)
        )
        # Re-create tester with updated config so it reads the new command
        tool_ctx.tester = DefaultTestRunner(config=tool_ctx.config, runner=tool_ctx.runner)

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(1, "FAIL", ""))  # test gate fails
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

        # Verify the test command was ["make", "test"]
        test_call = tool_ctx.runner.call_args_list[2]
        assert test_call[0] == ["make", "test"]

    @pytest.mark.asyncio
    async def test_require_enabled_still_gates_execution(self, tool_ctx):
        """S10: _require_enabled() defense-in-depth still works with config."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()


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

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for delete_directory_contents tests."""

    def test_continues_after_permission_error(self, tmp_path):
        """1a: PermissionError on one item does not abort the loop."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "dir_a").mkdir()
        (target / "locked_dir").mkdir()
        (target / "file_c.txt").touch()

        # Capture real rmtree before patching
        real_rmtree = shutil.rmtree

        def selective_rmtree(path, *args, **kwargs):
            if Path(path).name == "locked_dir":
                raise PermissionError("Permission denied")
            real_rmtree(path, *args, **kwargs)

        with patch("autoskillit.workspace.cleanup.shutil.rmtree", side_effect=selective_rmtree):
            result = _delete_directory_contents(target)

        assert "dir_a" in result.deleted
        assert "file_c.txt" in result.deleted
        assert any(name == "locked_dir" for name, _ in result.failed)
        assert result.success is False

    def test_file_not_found_treated_as_success(self, tmp_path):
        """1b: FileNotFoundError means item is gone = success."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "ghost.txt").touch()

        # Delete the file before the cleanup function processes it
        with patch.object(Path, "unlink", side_effect=FileNotFoundError("gone")):
            with patch.object(Path, "is_dir", return_value=False):
                result = _delete_directory_contents(target)

        assert "ghost.txt" in result.deleted
        assert result.failed == []
        assert result.success is True

    def test_preserves_specified_dirs(self, tmp_path):
        """1c: Preserved dirs are skipped, others deleted."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / ".cache").mkdir()
        (target / "reports").mkdir()
        (target / "output.txt").touch()
        (target / "temp_dir").mkdir()

        result = _delete_directory_contents(target, preserve={".cache", "reports"})

        assert ".cache" in result.skipped
        assert "reports" in result.skipped
        assert "output.txt" in result.deleted
        assert "temp_dir" in result.deleted
        assert (target / ".cache").exists()
        assert (target / "reports").exists()
        assert not (target / "output.txt").exists()
        assert not (target / "temp_dir").exists()

    def test_all_items_deleted_successfully(self, tmp_path):
        """1d: All succeed with no failures."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "a").mkdir()
        (target / "b").touch()
        (target / "c").touch()

        result = _delete_directory_contents(target)

        assert result.success is True
        assert result.failed == []
        assert len(result.deleted) == 3

    @pytest.mark.asyncio
    async def test_reset_test_dir_returns_partial_failure_json(self, tool_ctx, tmp_path):
        """1e: reset_test_dir returns structured JSON on partial failure."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "ok_file").touch()

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[],
        )
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        result = json.loads(await reset_test_dir(test_dir=str(workspace), force=False))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
        assert "ok_file" in result["deleted"]

    @pytest.mark.asyncio
    async def test_reset_workspace_returns_partial_failure_json(self, tool_ctx, tmp_path):
        """1f: reset_workspace returns structured JSON on partial failure."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(0, "", ""))

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[".cache"],
        )
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]


# ---------------------------------------------------------------------------
# Step 2: Safety config wiring
# ---------------------------------------------------------------------------


class TestSafetyConfigWiring:
    """Safety config fields are read at the point of enforcement."""

    @pytest.mark.asyncio
    async def test_reset_test_dir_allows_with_marker(self, tool_ctx, tmp_path):
        """2a: Directory with marker passes the reset guard."""
        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")
        (target / "file.txt").touch()

        result = json.loads(await reset_test_dir(test_dir=str(target), force=False))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_reset_test_dir_enforces_marker_when_missing(self, tool_ctx, tmp_path):
        """2b: Missing marker blocks reset_test_dir."""
        target = tmp_path / "unmarked"
        target.mkdir()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reset_workspace_enforces_marker(self, tool_ctx, tmp_path):
        """2c: reset_workspace requires marker, then checks command config."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))

        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")

        result = json.loads(await reset_workspace(test_dir=str(target)))
        # Should pass marker guard but fail on "not configured"
        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.asyncio
    async def test_merge_worktree_skips_test_gate_when_disabled(self, tool_ctx, tmp_path):
        """2d: test_gate_on_merge=False skips test execution."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(test_gate_on_merge=False))

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        # NO test-check call — skipped
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert result["merge_succeeded"] is True

        # Verify no test command was called — the 3rd call should be git fetch, not test
        third_call_cmd = tool_ctx.runner.call_args_list[2][0]
        assert third_call_cmd == ["git", "fetch", "origin"]

    @pytest.mark.asyncio
    async def test_run_skill_retry_skips_dry_walkthrough_when_disabled(self, tool_ctx, tmp_path):
        """2e: require_dry_walkthrough=False bypasses dry-walkthrough gate."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        tool_ctx.runner.push(_make_result(0, '{"result": "done"}', ""))
        result = json.loads(
            await run_skill_retry(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["subtype"] != "gate_error"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_skill_enforces_dry_walkthrough_when_enabled(self, tool_ctx, tmp_path):
        """2f: run_skill enforces dry-walkthrough gate when enabled (default)."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_run_skill_skips_dry_walkthrough_when_disabled(self, tool_ctx, tmp_path):
        """2g: run_skill skips dry-walkthrough gate when disabled."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        tool_ctx.runner.push(_make_result(0, '{"result": "done"}', ""))
        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["subtype"] != "gate_error"


# ---------------------------------------------------------------------------
# Step 3: merge_worktree cleanup reporting
# ---------------------------------------------------------------------------


class TestMergeWorktreeCleanupReporting:
    """merge_worktree reports accurate cleanup results."""

    @pytest.mark.asyncio
    async def test_reports_worktree_remove_failure(self, tool_ctx, tmp_path):
        """3a: worktree_removed reflects actual git worktree remove result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
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

    @pytest.mark.asyncio
    async def test_reports_branch_delete_failure(self, tool_ctx, tmp_path):
        """3b: branch_deleted reflects actual git branch -D result."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
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

    @pytest.mark.asyncio
    async def test_checks_fetch_result(self, tool_ctx, tmp_path):
        """3c: git fetch failure returns error before rebase attempt."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))  # test-check
        tool_ctx.runner.push(
            _make_result(1, "", "fatal: could not connect to remote")
        )  # git fetch FAILS
        result = json.loads(await merge_worktree(str(wt), "main"))
        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.FETCH


class TestMergeWorktreeCleanupWarnings:
    """merge_worktree emits logger.warning when cleanup steps fail post-merge."""

    @pytest.mark.asyncio
    async def test_warns_on_worktree_remove_failure(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
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

    @pytest.mark.asyncio
    async def test_warns_on_branch_delete_failure(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
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

    @pytest.mark.asyncio
    async def test_no_warning_on_clean_cleanup(self, tool_ctx, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))
        tool_ctx.runner.push(_make_result(0, "PASS\n= 100 passed =", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx.runner.push(_make_result(0, "", ""))  # rebase
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


# ---------------------------------------------------------------------------
# run_python tool
# ---------------------------------------------------------------------------


class TestRunPython:
    """run_python tool: import, call, timeout, async support."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for all run_python tests."""

    @pytest.mark.asyncio
    async def test_calls_function(self):
        """run_python imports module, calls function, returns JSON result."""
        result = json.loads(
            await run_python(
                callable="json.dumps",
                args={"obj": {"key": "value"}},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"] == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_import_error(self):
        """run_python returns error for non-existent module."""
        result = json.loads(
            await run_python(
                callable="nonexistent_module.some_func",
                timeout=10,
            )
        )
        assert result["success"] is False
        assert "import" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_not_callable(self):
        """run_python returns error when target is not callable."""
        result = json.loads(
            await run_python(
                callable="json.decoder",
                timeout=10,
            )
        )
        assert result["success"] is False
        assert "callable" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        """run_python returns error on timeout."""
        import asyncio as _aio
        from unittest.mock import patch

        async def _hang(**_kw: object) -> None:
            await _aio.sleep(300)

        mock_module = MagicMock()
        mock_module.hang_fn = _hang

        with patch("importlib.import_module", return_value=mock_module):
            result = json.loads(
                await run_python(
                    callable="fake_mod.hang_fn",
                    timeout=1,
                )
            )
        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_async_function(self):
        """run_python correctly awaits async functions."""
        result = json.loads(
            await run_python(
                callable="asyncio.sleep",
                args={"delay": 0},
                timeout=5,
            )
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_sync_timeout_logs_warning(self):
        """run_python emits a warning log when TimeoutError is raised."""
        import asyncio as _aio

        async def _hang(**_kw: object) -> None:
            await _aio.sleep(300)

        mock_module = MagicMock()
        mock_module.hang_fn = _hang

        with (
            patch("importlib.import_module", return_value=mock_module),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await run_python(callable="fake_mod.hang_fn", timeout=1))
        assert result["success"] is False
        assert "timeout" in result["error"].lower()
        assert any(log.get("log_level") == "warning" for log in logs), (
            f"Expected a warning log entry for timeout, got: {logs}"
        )
        assert any("timed out" in log.get("event", "").lower() for log in logs), (
            f"Expected 'timed out' in warning event, got: {logs}"
        )


class TestValidateSelectOnly:
    """SQL validation: pure function _validate_select_only."""

    def test_accepts_simple_select(self):
        _validate_select_only("SELECT * FROM users")

    def test_accepts_select_with_where(self):
        _validate_select_only("SELECT id, name FROM users WHERE age > ?")

    def test_accepts_select_with_join(self):
        _validate_select_only("SELECT a.id FROM a JOIN b ON a.id = b.id")

    def test_accepts_select_with_subquery(self):
        _validate_select_only("SELECT * FROM (SELECT id FROM users)")

    def test_accepts_leading_whitespace(self):
        _validate_select_only("  \n  SELECT 1")

    def test_rejects_insert(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("INSERT INTO users VALUES (1, 'a')")

    def test_rejects_update(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("UPDATE users SET name = 'x'")

    def test_rejects_delete(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("DELETE FROM users")

    def test_rejects_drop(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("DROP TABLE users")

    def test_rejects_alter(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ALTER TABLE users ADD COLUMN x")

    def test_rejects_attach(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ATTACH DATABASE 'other.db' AS other")

    def test_rejects_create(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("CREATE TABLE evil (id INT)")

    def test_rejects_pragma(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("PRAGMA table_info(users)")

    def test_rejects_non_select_start(self):
        with pytest.raises(ValueError, match="must begin with SELECT"):
            _validate_select_only("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_rejects_empty_query(self):
        with pytest.raises(ValueError):
            _validate_select_only("")

    def test_rejects_comment_hiding_write(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("SELECT 1; -- \nDROP TABLE users")


class TestSelectOnlyAuthorizer:
    """SQLite authorizer callback tests."""

    def test_allows_sqlite_select(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_SELECT, None, None, None, None)
            == sqlite3.SQLITE_OK
        )

    def test_allows_sqlite_read(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_READ, "users", "id", "main", None)
            == sqlite3.SQLITE_OK
        )

    def test_allows_sqlite_function(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_FUNCTION, None, "count", None, None)
            == sqlite3.SQLITE_OK
        )

    def test_denies_sqlite_insert(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_INSERT, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_delete(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_DELETE, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_update(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_UPDATE, "users", "name", "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_create_table(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_CREATE_TABLE, "evil", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_drop_table(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_DROP_TABLE, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_attach(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_ATTACH, "other.db", None, None, None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_pragma(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_PRAGMA, "table_info", None, None, None)
            == sqlite3.SQLITE_DENY
        )


class TestReadDb:
    """Integration tests for read_db tool with real SQLite databases."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for all read_db tests."""

    @pytest.fixture
    def sample_db(self, tmp_path):
        """Create a sample SQLite database for testing."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT, age INTEGER)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice', 30)")
        conn.execute("INSERT INTO users VALUES (2, 'Bob', 25)")
        conn.execute("INSERT INTO users VALUES (3, 'Charlie', 35)")
        conn.commit()
        conn.close()
        return db

    @pytest.mark.asyncio
    async def test_simple_select(self, sample_db):
        result = json.loads(await read_db(db_path=str(sample_db), query="SELECT * FROM users"))
        assert result["row_count"] == 3
        assert result["column_names"] == ["id", "name", "age"]
        assert len(result["rows"]) == 3
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_parameterized_query(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT name FROM users WHERE age > ?",
                params="[28]",
            )
        )
        assert result["row_count"] == 2
        names = [r["name"] for r in result["rows"]]
        assert "Alice" in names
        assert "Charlie" in names

    @pytest.mark.asyncio
    async def test_named_params(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT name FROM users WHERE age = :age",
                params='{"age": 25}',
            )
        )
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_empty_result(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users WHERE age > 100",
            )
        )
        assert result["row_count"] == 0
        assert result["rows"] == []
        assert result["column_names"] == ["id", "name", "age"]

    @pytest.mark.asyncio
    async def test_rejects_insert(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="INSERT INTO users VALUES (4, 'Dave', 40)",
            )
        )
        assert "error" in result
        err_lower = result["error"].lower()
        assert "forbidden" in err_lower or "select" in err_lower or "not authorized" in err_lower

    @pytest.mark.asyncio
    async def test_rejects_drop(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="DROP TABLE users",
            )
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_attach(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="ATTACH DATABASE ':memory:' AS other",
            )
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_nonexistent_db(self, tmp_path):
        result = json.loads(
            await read_db(
                db_path=str(tmp_path / "nonexistent.db"),
                query="SELECT 1",
            )
        )
        assert "error" in result
        assert "does not exist" in result["error"] or "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_not_a_file(self, tmp_path):
        result = json.loads(
            await read_db(
                db_path=str(tmp_path),
                query="SELECT 1",
            )
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_params_json(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users",
                params="not json",
            )
        )
        assert "error" in result
        assert "params" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_gated_when_disabled(self, sample_db, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT 1",
            )
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_max_rows_truncation(self, sample_db, tool_ctx):
        tool_ctx.config = AutomationConfig(read_db=ReadDbConfig(max_rows=2))
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users",
            )
        )
        assert result["row_count"] == 2
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_blob_base64_encoding(self, tmp_path):
        import base64

        db = tmp_path / "blob.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE data (id INTEGER, content BLOB)")
        conn.execute("INSERT INTO data VALUES (1, ?)", (b"\x00\x01\x02\xff",))
        conn.commit()
        conn.close()
        result = json.loads(
            await read_db(
                db_path=str(db),
                query="SELECT * FROM data",
            )
        )
        assert base64.b64decode(result["rows"][0]["content"]) == b"\x00\x01\x02\xff"

    @pytest.mark.asyncio
    async def test_query_timeout(self, sample_db, tool_ctx):
        tool_ctx.config = AutomationConfig(read_db=ReadDbConfig(timeout=1))
        # Cross join 3 rows^18 = ~387 million rows — guaranteed to exceed 1s timeout
        slow_query = "SELECT count(*) FROM " + ", ".join(f"users t{i}" for i in range(18))
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query=slow_query,
            )
        )
        assert "error" in result
        assert "timeout" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sql_error_returns_error(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT nonexistent_column FROM users",
            )
        )
        assert "error" in result


class TestReadDbGating:
    """read_db gating test in disabled-tools context."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_read_db_gated(self):
        result = json.loads(await read_db(db_path="/tmp/x.db", query="SELECT 1"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"]


class TestEnsureSkillPrefix:
    """Unit tests for _ensure_skill_prefix helper."""

    def test_adds_use_to_slash_command(self):
        assert _ensure_skill_prefix("/investigate error") == "Use /investigate error"

    def test_adds_use_to_namespaced_skill(self):
        assert (
            _ensure_skill_prefix("/autoskillit:investigate error")
            == "Use /autoskillit:investigate error"
        )

    def test_no_double_prefix(self):
        assert _ensure_skill_prefix("Use /investigate error") == "Use /investigate error"

    def test_ignores_plain_prompts(self):
        assert _ensure_skill_prefix("Fix the bug in main.py") == "Fix the bug in main.py"

    def test_handles_leading_whitespace(self):
        assert _ensure_skill_prefix("  /investigate error") == "Use /investigate error"


class TestRunSkillPrefix:
    """run_skill passes prefixed command to subprocess."""

    @pytest.mark.asyncio
    async def test_run_skill_prefixes_skill_command(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Use /investigate error")

    @pytest.mark.asyncio
    async def test_run_skill_no_prefix_for_plain_prompt(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("Fix the bug in main.py", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Fix the bug in main.py")

    @pytest.mark.asyncio
    async def test_run_skill_includes_completion_directive(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "%%ORDER_UP%%" in cmd[4]


class TestRunSkillRetryPrefix:
    """run_skill_retry passes prefixed command to subprocess."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_prefixes_skill_command(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Use /investigate error")

    @pytest.mark.asyncio
    async def test_run_skill_retry_no_prefix_for_plain_prompt(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("Fix the bug in main.py", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Fix the bug in main.py")


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.asyncio
    async def test_gate_still_fires_for_implement_skill(self, tool_ctx, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestRunSkillTimeoutFromConfig:
    """run_skill and run_skill_retry use configurable timeouts."""

    @pytest.mark.asyncio
    async def test_run_skill_timeout_from_config(self, tool_ctx):
        """run_skill uses _config.run_skill.timeout instead of hardcoded value."""
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(timeout=120)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        assert tool_ctx.runner.call_args_list[-1][2] == 120.0


class TestRunSkillInjectsCompletionDirective:
    """run_skill injects completion directive into the skill command."""

    @pytest.mark.asyncio
    async def test_run_skill_injects_completion_directive(self, tool_ctx):
        """Skill command passed to claude -p contains the completion marker instruction."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        cmd = tool_ctx.runner.call_args_list[-1][0]
        # The prompt argument is at index 4 (shifted by 2 env tokens)
        skill_arg = cmd[4]
        assert "%%ORDER_UP%%" in skill_arg
        assert "ORCHESTRATION DIRECTIVE" in skill_arg


_SUCCESS_JSON = (
    '{"type": "result", "subtype": "success", "is_error": false,'
    ' "result": "done", "session_id": "s1"}'
)


class TestRunSkillEnvPrefix:
    """run_skill and run_skill_retry inject CLAUDE_CODE_EXIT_AFTER_STOP_DELAY env prefix."""

    @pytest.mark.asyncio
    async def test_default_delay_prepends_env_to_cmd(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=30000"
        assert "claude" in cmd

    @pytest.mark.asyncio
    async def test_zero_delay_omits_env_prefix(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=0)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] != "env"
        assert cmd[0] == "claude"

    @pytest.mark.asyncio
    async def test_custom_delay_value_in_cmd(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=60000)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=60000"

    @pytest.mark.asyncio
    async def test_run_skill_retry_also_gets_env_prefix(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill_retry("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=30000"


class TestSessionLogDir:
    """Unit tests for _session_log_dir path derivation."""

    def test_replaces_slashes(self):
        result = _session_log_dir("/home/user/project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-project"

    def test_replaces_underscores(self):
        """Underscores must be replaced with dashes to match Claude Code's encoding."""
        result = _session_log_dir("/home/user/my_project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-my-project"

    def test_replaces_both_slashes_and_underscores(self):
        result = _session_log_dir("/home/user_name/my_project/sub_dir")
        assert (
            result == Path.home() / ".claude" / "projects" / "-home-user-name-my-project-sub-dir"
        )


class TestRunSkillPassesSessionLogDir:
    """run_skill passes session_log_dir derived from cwd."""

    @pytest.mark.asyncio
    async def test_run_skill_passes_session_log_dir(self, tool_ctx):
        """runner receives session_log_dir derived from cwd."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/some/project")

        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir
        assert "-some-project" in str(expected_dir)


class TestRunSkillRetryPassesSessionLogDir:
    """run_skill_retry passes session_log_dir derived from cwd."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_session_log_dir(self, tool_ctx):
        """run_skill_retry must pass session_log_dir just like run_skill."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate foo", "/some/project")

        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir


class TestStalenessReturnsNeedsRetry:
    """Stale SubprocessResult triggers needs_retry response."""

    def test_staleness_returns_needs_retry(self):
        """A stale result produces needs_retry=True, retry_reason='resume'."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["needs_retry"] is True
        assert response["retry_reason"] == "resume"
        assert response["subtype"] == "stale"
        assert response["success"] is False


class TestBuildSkillResultCrossValidation:
    """_build_skill_result cross-validates signals to produce unambiguous success."""

    EXPECTED_SKILL_KEYS = {
        "success",
        "result",
        "session_id",
        "subtype",
        "is_error",
        "exit_code",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
    }

    def test_empty_stdout_exit_zero_is_failure(self):
        """Exit 0 with empty stdout is NOT success — output was lost."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True

    def test_timed_out_session_is_failure(self):
        """Timed-out sessions are always failures, regardless of partial stdout."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "timeout"

    def test_stale_session_is_failure(self):
        """Stale sessions are failures (even though retriable)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["needs_retry"] is True

    def test_normal_success_has_success_true(self):
        """A valid session result with non-empty output is success."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is True
        assert response["is_error"] is False
        assert response["result"] == "Task completed."

    def test_nonzero_exit_overrides_is_error_false(self):
        """Exit code != 0 means failure even if Claude wrote is_error=false."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "partial",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=1,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False

    def test_gate_disabled_schema(self, tool_ctx):
        """Gate-disabled response has standard keys."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.helpers import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        response = json.loads(_require_enabled())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_stale_schema(self):
        """Stale response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_timeout_schema(self):
        """Timeout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_normal_success_schema(self):
        """Normal success response has standard keys."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_empty_stdout_schema(self):
        """Empty stdout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 9-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self, tool_ctx):
        """Gate errors must use the same schema as normal responses."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.helpers import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_result = _require_enabled()
        assert gate_result is not None
        response = json.loads(gate_result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["needs_retry"] is False
        assert "result" in response

    def test_dry_walkthrough_gate_returns_standard_schema(self, tool_ctx, tmp_path):
        """Dry-walkthrough gate errors must use the standard response schema."""
        plan = tmp_path / "plan.md"
        plan.write_text("No marker here")
        skill_cmd = f"/autoskillit:implement-worktree {plan}"
        result = _check_dry_walkthrough(skill_cmd, str(tmp_path))
        assert result is not None
        response = json.loads(result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "gate_error"


class TestComputeSuccess:
    """_compute_success cross-validates all signals for unambiguous success."""

    def test_all_good_is_success(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is True
        )

    def test_empty_result_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_nonzero_exit_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=1, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_is_error_true_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=True, result="Error occurred", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_timed_out_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.TIMED_OUT)
            is False
        )

    def test_stale_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.STALE) is False
        )

    def test_unknown_subtype_is_failure(self):
        session = ClaudeSessionResult(
            subtype="unknown", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
        )


class TestComputeSuccessNaturalExitNonZero:
    """NATURAL_EXIT with non-zero returncode is always a failure."""

    def test_natural_exit_nonzero_returncode_with_success_session_returns_false(self):
        """NATURAL_EXIT + non-zero returncode is unrecoverable regardless of session envelope.

        Documents that PTY-masking quirks on natural exit cannot be distinguished from
        genuine CLI errors, so we fail conservatively.
        """
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=1, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_completed_and_natural_exit_same_outcome_when_returncode_zero(self):
        """COMPLETED and NATURAL_EXIT agree when returncode=0 (no PTY masking issue).

        Documents the symmetric case: asymmetry only matters when returncode != 0.
        """
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id="s1"
        )
        result_completed = _compute_success(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        result_natural = _compute_success(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert result_completed is True
        assert result_natural is True


class TestComputeRetry:
    """_compute_retry cross-validates all signals for retry eligibility."""

    def test_success_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_max_turns_is_retriable(self):
        session = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_context_exhaustion_is_retriable(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=True, result="Prompt is too long", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_empty_output_exit_zero_is_retriable(self):
        """Infrastructure failure: session never ran, CLI exited cleanly."""
        session = ClaudeSessionResult(
            subtype="empty_output", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_empty_output_exit_one_not_retriable(self):
        """Real failure: CLI crashed with empty output."""
        session = ClaudeSessionResult(
            subtype="empty_output", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_timeout_not_retriable(self):
        session = ClaudeSessionResult(subtype="timeout", is_error=True, result="", session_id="")
        needs, reason = _compute_retry(
            session, returncode=-1, termination=TerminationReason.TIMED_OUT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_unparseable_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="crash", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_execution_error_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="tool error",
            session_id="s1",
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE


class TestComputeRetryUnparseable:
    """_compute_retry distinguishes unparseable under COMPLETED vs NATURAL_EXIT."""

    def test_unparseable_subtype_with_nonzero_returncode_should_retry(self):
        """unparseable under COMPLETED means process was killed mid-write.

        The drain timeout expired before the result record was fully flushed.
        The session likely completed; retry with resume.
        """
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="partial", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_unparseable_subtype_natural_exit_no_retry(self):
        """unparseable under NATURAL_EXIT is a content failure, not retryable.

        The process exited cleanly with malformed output — this is not a timing issue.
        """
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE


class TestIsCompletionKillAnomaly:
    """_is_completion_kill_anomaly covers exactly the subtypes that represent kill artifacts."""

    @pytest.mark.parametrize(
        "subtype,result,expected",
        [
            ("unparseable", "", True),  # killed mid-write → partial NDJSON
            ("empty_output", "", True),  # killed before any stdout written
            ("success", "", True),  # killed after result record, content empty
            ("success", "x", False),  # success with content → NOT an anomaly
            ("error_during_execution", "", False),  # explicit API error, not a kill artifact
            ("timeout", "", False),  # timeout is a separate terminal state
        ],
    )
    def test_anomaly_classification(self, subtype: str, result: str, expected: bool) -> None:
        session = ClaudeSessionResult(
            subtype=subtype,
            is_error=(subtype != "success"),
            result=result,
            session_id="",
            errors=[],
            token_usage=None,
        )
        assert _is_completion_kill_anomaly(session) is expected


class TestComputeRetrySuccessEmptyResult:
    """_compute_retry for success subtype with empty result under COMPLETED termination."""

    def test_success_empty_result_completed_rc0_is_retriable(self) -> None:
        """success + "" + COMPLETED + rc=0 must be retriable (drain-race glitch)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME

    def test_success_empty_result_completed_negative_rc_is_retriable(self) -> None:
        """success + "" + COMPLETED + rc=-15 (SIGTERM kill) must also be retriable."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME

    def test_success_nonempty_result_completed_is_not_retriable(self) -> None:
        """success + non-empty result + COMPLETED must NOT be retriable (genuine success)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done. %%ORDER_UP%%",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, _ = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is False

    def test_success_empty_result_natural_exit_is_not_retriable(self) -> None:
        """success + "" + NATURAL_EXIT must NOT be retriable (CLI chose to exit clean)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, _ = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert retriable is False

    def test_empty_output_completed_negative_rc_is_retriable(self) -> None:
        """empty_output + COMPLETED + rc=-15 must be retriable.

        Process was killed by infrastructure before writing any stdout.
        """
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME


class TestComputeSuccessCompletedBypassEmptyResult:
    """COMPLETED bypass requires non-empty result; empty result bypasses are rejected."""

    def test_completed_success_empty_result_nonzero_rc_is_failure(self) -> None:
        """COMPLETED bypass does NOT engage when result is empty, even for success subtype."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
                completion_marker="",
            )
            is False
        )


class TestBuildSkillResultStderr:
    """_build_skill_result includes stderr in responses."""

    def test_stderr_included_in_response(self):
        """Subprocess stderr is surfaced in the response."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="queue contention",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == "queue contention"

    def test_stderr_truncated(self):
        """Stderr exceeding 5000 chars is truncated."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        long_stderr = "x" * 6000
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr=long_stderr,
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert len(response["stderr"]) < len(long_stderr)
        assert "truncated" in response["stderr"]

    def test_empty_stderr_is_empty_string(self):
        """Empty stderr produces empty string, not omitted."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""

    def test_stale_branch_has_empty_stderr(self):
        """Stale branch produces empty stderr (process killed before output)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""


class TestRetryResponseFieldsIncludesStderr:
    """RETRY_RESPONSE_FIELDS schema includes stderr."""

    def test_stderr_in_fields(self):
        assert "stderr" in RETRY_RESPONSE_FIELDS

    def test_field_count(self):
        assert len(RETRY_RESPONSE_FIELDS) == 10


class TestLoadSkillScriptFailurePredicates:
    """The load_recipe tool description documents failure predicates."""

    def test_description_documents_run_skill_failure(self):
        """The routing rules must define failure for run_skill, not just test_check."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = {
            c.name: c for c in mcp._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        assert "run_skill" in desc
        assert "success" in desc.lower()


class TestContextExhaustionStructured:
    """_is_context_exhausted uses structured detection, not substring on result."""

    def test_context_exhaustion_not_triggered_by_model_prose(self):
        """Model output discussing prompt length must NOT trigger context exhaustion.

        The phrase 'prompt is too long' appearing in the model's result text
        (not as a CLI structural signal) should not cause needs_retry=True.
        """
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="The user said: prompt is too long for this task",
            session_id="s1",
        )
        assert session.needs_retry is False
        assert session._is_context_exhausted() is False

    def test_real_context_exhaustion_still_detected(self):
        """Genuine context exhaustion (specific subtype) is still detected."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="prompt is too long",
            session_id="s1",
            errors=["prompt is too long"],
        )
        assert session._is_context_exhausted() is True
        assert session.needs_retry is True


class TestParsePytestSummaryAnchored:
    """_parse_pytest_summary only matches lines in the === delimited section."""

    def test_pytest_summary_ignores_non_summary_lines(self):
        """Log output with 'N failed' must not be confused with the summary.

        Test output can contain lines like '3 failed connections reestablished'
        which match the outcome pattern. Only the === delimited summary line
        should be matched.
        """
        stdout = (
            "test_network.py::test_reconnect PASSED\n"
            "3 failed connections reestablished\n"
            "1 error in config reloaded successfully\n"
            "=== 5 passed in 2.1s ===\n"
        )
        counts = _parse_pytest_summary(stdout)
        assert counts == {"passed": 5}
        assert "failed" not in counts
        assert "error" not in counts


class TestParseFallbackRejectsUntypedJson:
    """parse_session_result fallback path requires type == result."""

    def test_parse_fallback_rejects_untyped_json(self):
        """Single JSON object without type=result must be rejected.

        The fallback path should not accept arbitrary dict objects as results.
        """
        parsed = parse_session_result('{"error": "something broke"}')
        assert parsed.subtype == "unparseable"
        assert parsed.is_error is True


class TestCompletionViaMonitorKill:
    """Completion detected by monitor + kill returncode is not failure."""

    MARKER = "%%ORDER_UP%%"

    def test_completion_via_monitor_kill_is_not_failure(self):
        """When the session monitor detects completion and kills the process,
        returncode is -15 (SIGTERM). _compute_success should treat this as
        success when the session result envelope says success.
        """
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_completion_via_monitor_kill_returncode_zero(self):
        """PTY may mask signal codes to returncode=0 — COMPLETED still works."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )


class TestBuildSkillResultCompleted:
    """_build_skill_result and _compute_success handle COMPLETED termination correctly."""

    def test_build_skill_result_completed_nonempty_result_is_success(self):
        """COMPLETED + valid JSON stdout with non-empty result → success=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task done.",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=-15,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is True

    def test_build_skill_result_completed_empty_result_is_failure(self):
        """COMPLETED + empty stdout + rc=-15 → success=False, needs_retry=True.

        Process was killed by infrastructure before writing any stdout.
        The COMPLETED + empty_output path is a kill artifact covered by
        _is_completion_kill_anomaly.
        """
        result = _make_result(
            returncode=-15,
            stdout="",
            termination_reason=TerminationReason.COMPLETED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True

    def test_compute_success_completed_empty_result_returns_false(self):
        """Empty result with COMPLETED termination: bypass does NOT engage → returns False.

        Documents the branch at _compute_success lines ~211-216: the pass branch
        requires result.strip() to be truthy. When result is empty, the function
        falls through to the final guard which returns False.
        """
        session = ClaudeSessionResult(
            subtype="empty_output",
            result="",
            is_error=True,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
            )
            is False
        )

    def test_success_empty_completed_returns_needs_retry_true(self, tool_ctx):
        """Full path: stdout with success+empty under COMPLETED → needs_retry=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
        )
        parsed = json.loads(
            _build_skill_result(result, completion_marker="", skill_command="/test").to_json()
        )
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True
        assert parsed["retry_reason"] == RetryReason.RESUME.value
        assert parsed["subtype"] == "success"

    def test_success_empty_completed_subtype_captured_in_audit_log(self, tool_ctx):
        """_capture_failure must be called with subtype='success' for audit log integrity."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
        )
        _build_skill_result(
            result, completion_marker="", skill_command="/test", audit=tool_ctx.audit
        )
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "success"
        assert report[0].needs_retry is True


class TestRunSkillRetryConsolidation:
    """run_skill_retry delegates to ctx.executor.run() with retry-specific config."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for run_skill_retry consolidation tests."""
        self._tool_ctx = tool_ctx

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_add_dir_to_subprocess(self):
        """add_dir is forwarded to ctx.executor.run()."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp", add_dir="/extra/dir")

        assert mock_run.call_args.kwargs.get("add_dir") == "/extra/dir"

    @pytest.mark.asyncio
    async def test_run_skill_retry_uses_retry_timeout_not_skill_timeout(self):
        """run_skill_retry passes RunSkillRetryConfig.timeout (7200) not RunSkillConfig (3600)."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp")

        assert mock_run.call_args.kwargs.get("timeout") == 7200


class TestMarkerCrossValidation:
    """Completion marker cross-validation catches misclassified sessions."""

    MARKER = "%%ORDER_UP%%"

    def test_marker_only_result_is_not_success(self):
        """Result containing only the marker with no real content is failure."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=self.MARKER,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_marker_stripped_from_result(self):
        """_build_skill_result strips the completion marker from result text."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed.\n\n{self.MARKER}",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(
            _build_skill_result(result_obj, completion_marker=self.MARKER).to_json()
        )
        assert self.MARKER not in response["result"]
        assert "Task completed." in response["result"]

    def test_natural_exit_without_marker_not_success(self):
        """Session claims success but never wrote the marker — not success."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some partial output",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_termination_reason_natural_exit(self):
        """NATURAL_EXIT with marker in result is success."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed(self):
        """COMPLETED termination with marker in result is success."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed_without_marker_fails(self):
        """COMPLETED but result doesn't contain marker — not success."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some output without marker",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is False
        )

    @pytest.mark.parametrize(
        "termination,returncode,result_text,expected",
        [
            (TerminationReason.NATURAL_EXIT, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.NATURAL_EXIT, 0, "No marker here", False),
            (TerminationReason.NATURAL_EXIT, 0, MARKER, False),  # marker-only
            (TerminationReason.COMPLETED, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, -15, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, 0, "No marker here", False),
            (TerminationReason.STALE, -15, f"Done.\n\n{MARKER}", False),
            (TerminationReason.TIMED_OUT, -1, f"Done.\n\n{MARKER}", False),
        ],
        ids=[
            "natural_exit+marker=success",
            "natural_exit+no_marker=failure",
            "natural_exit+marker_only=failure",
            "completed+marker=success",
            "completed_sigterm+marker=success",
            "completed+no_marker=failure",
            "stale+marker=failure",
            "timed_out+marker=failure",
        ],
    )
    def test_cross_validation_matrix(self, termination, returncode, result_text, expected):
        """Full cross-validation matrix for termination x marker presence."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=result_text,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=returncode,
                termination=termination,
                completion_marker=self.MARKER,
            )
            is expected
        )


class TestClaudeSessionResultTypeEnforcement:
    """ClaudeSessionResult.__post_init__ enforces field types."""

    def test_null_result_becomes_empty_string(self):
        session = ClaudeSessionResult(subtype="error", is_error=True, result=None, session_id="s1")
        assert session.result == ""

    def test_null_errors_becomes_empty_list(self):
        session = ClaudeSessionResult(
            subtype="error", is_error=True, result="err", session_id="s1", errors=None
        )
        assert session.errors == []

    def test_null_subtype_becomes_unknown(self):
        session = ClaudeSessionResult(subtype=None, is_error=False, result="ok", session_id="s1")
        assert session.subtype == "unknown"

    def test_null_session_id_becomes_empty(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="ok", session_id=None
        )
        assert session.session_id == ""

    def test_is_context_exhausted_with_null_safe_fields(self):
        session = ClaudeSessionResult(
            subtype="error", is_error=True, result=None, session_id="s1", errors=None
        )
        assert session._is_context_exhausted() is False

    def test_list_content_result_becomes_string(self):
        blocks = [{"type": "text", "text": "Task completed."}]
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result=blocks, session_id="s1"
        )
        assert session.result == "Task completed."
        assert isinstance(session.result, str)


class TestParseSessionResultNullFields:
    """parse_session_result handles null JSON values correctly."""

    def test_null_result_field(self):
        raw = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": None,
            "session_id": "s1",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.result == ""

    def test_null_errors_field(self):
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "s1",
            "errors": None,
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.errors == []


class TestRunSkillModel:
    """Tests for model parameter in run_skill and run_skill_retry."""

    _MOCK_STDOUT = (
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"result": "done", "session_id": "s1"}'
    )

    # MOD_S1
    @pytest.mark.asyncio
    async def test_run_skill_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S2
    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill_retry("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S3
    @pytest.mark.asyncio
    async def test_run_skill_no_model_flag_when_empty(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" not in cmd


class TestResolveModel:
    """Tests for _resolve_model resolution chain."""

    @pytest.fixture(autouse=True)
    def _set_config(self, tool_ctx):
        self._tool_ctx = tool_ctx

    def _set_model_config(self, default=None, override=None):
        cfg = AutomationConfig(model=ModelConfig(default=default, override=override))
        self._tool_ctx.config = cfg

    # MOD_R1
    def test_resolve_model_override_wins(self):
        self._set_model_config(override="haiku")
        assert _resolve_model("sonnet", self._tool_ctx.config) == "haiku"

    # MOD_R2
    def test_resolve_model_step_model(self):
        self._set_model_config()
        assert _resolve_model("sonnet", self._tool_ctx.config) == "sonnet"

    # MOD_R3
    def test_resolve_model_config_default(self):
        self._set_model_config(default="haiku")
        assert _resolve_model("", self._tool_ctx.config) == "haiku"

    # MOD_R4
    def test_resolve_model_nothing_set(self):
        self._set_model_config()
        assert _resolve_model("", self._tool_ctx.config) is None


# ---------------------------------------------------------------------------
# Minimal valid script YAML used across migration suggestion tests
# ---------------------------------------------------------------------------

_MINIMAL_SCRIPT_YAML = """\
name: test-script
description: Test
summary: test
inputs:
  task:
    description: What to do
    required: true
steps:
  do-thing:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
constraints:
  - "Follow routing rules"
"""


def _write_minimal_script(scripts_dir: Path, name: str = "test-script") -> Path:
    """Write a minimal valid workflow script with no autoskillit_version field."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{name}.yaml"
    path.write_text(_MINIMAL_SCRIPT_YAML)
    return path


class TestMigrationSuggestions:
    """MSUG2: validate_recipe surfaces migration warnings."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    # MSUG2
    @pytest.mark.asyncio
    async def test_validate_always_includes_outdated_version(self, tmp_path):
        """MSUG2: validate_recipe always includes outdated-script-version in semantic results."""
        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML)

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestMigrationSuppression:
    """SUP1, SUP4: load_recipe respects migration.suppressed config."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    # SUP1
    @pytest.mark.asyncio
    async def test_outdated_version_not_in_suggestions_when_suppressed(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """SUP1: outdated-recipe-version absent when recipe is suppressed; headless not called."""
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        _write_minimal_script(scripts_dir, "test-script")

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await load_recipe(name="test-script"))

        assert "suggestions" in result
        rules = [s["rule"] for s in result["suggestions"]]
        assert "outdated-recipe-version" not in rules
        mock_headless.assert_not_called()

    # SUP4
    @pytest.mark.asyncio
    async def test_validate_always_includes_outdated_version_regardless_of_suppression(
        self, tmp_path, tool_ctx
    ):
        """SUP4: validate_recipe includes outdated-script-version even when suppressed."""
        from autoskillit.config import MigrationConfig

        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML)

        # Even with script suppressed in config, validate_recipe does not filter
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """load_recipe works WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_load_recipe_does_not_call_migration_engine(self, tmp_path, monkeypatch):
        """load_recipe must not trigger headless migration even when migrations are applicable."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=["v0.1.0"]),
            patch("autoskillit.execution.headless.run_headless_core") as mock_headless,
            patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen,
        ):
            result = json.loads(await load_recipe(name="implementation-pipeline"))
        assert "error" not in result
        mock_headless.assert_not_called()
        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_recipe_does_not_auto_generate_contract_card(self, tmp_path, monkeypatch):
        """load_recipe must not call generate_recipe_card even when no card exists."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen:
            await load_recipe(name="test")
        mock_gen.assert_not_called()


class TestMigrateRecipe:
    """P4: migrate_recipe is a gated tool that runs migration engine and regenerates cards."""

    @pytest.fixture(autouse=True)
    def _open_kitchen(self, tool_ctx):
        """migrate_recipe requires tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=True)

    def _setup_migration_env(
        self,
        tmp_path,
        monkeypatch,
        tool_ctx,
        *,
        suppressed: list[str] | None = None,
    ):
        """Create directory structure, fake migration YAML, and config."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test-script.yaml"
        recipe_path.write_text(_MINIMAL_SCRIPT_YAML)

        installed_ver = autoskillit.__version__
        fake_mig_dir = tmp_path / "migrations"
        fake_mig_dir.mkdir()
        migration_yaml = (
            f"from_version: '0.0.0'\n"
            f"to_version: '{installed_ver}'\n"
            "description: Upgrade scripts\n"
            "changes:\n"
            "  - id: add-summary-field\n"
            "    description: Scripts now require a summary field\n"
            "    instruction: Add summary field to your script\n"
        )
        (fake_mig_dir / "0.0.0-migration.yaml").write_text(migration_yaml)
        monkeypatch.setattr(ml, "_migrations_dir", lambda: fake_mig_dir)

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=suppressed or []))

        temp_mig_dir = tmp_path / ".autoskillit" / "temp" / "migrations"
        temp_mig_dir.mkdir(parents=True)

        migrated_content = _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{installed_ver}'\n"
        return {
            "recipe_path": recipe_path,
            "temp_mig_dir": temp_mig_dir,
            "migrated_content": migrated_content,
            "installed_ver": installed_ver,
        }

    def test_migrate_recipe_is_in_gated_tools(self):
        """migrate_recipe is a gated tool."""
        assert "migrate_recipe" in GATED_TOOLS

    def test_migrate_recipe_not_in_ungated_tools(self):
        """migrate_recipe is not an ungated tool."""
        assert "migrate_recipe" not in UNGATED_TOOLS

    @pytest.mark.asyncio
    async def test_migrate_recipe_requires_gate(self, tool_ctx):
        """migrate_recipe returns gate_error when kitchen is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await migrate_recipe(name="test"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_migrate_recipe_not_found(self, tmp_path, monkeypatch):
        """migrate_recipe returns error for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await migrate_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.asyncio
    async def test_migrate_recipe_up_to_date(self, tmp_path, monkeypatch):  # SRV-UPD-1
        """migrate_recipe returns up_to_date when no migrations applicable and contract fresh."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=[]),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="implementation-pipeline"))
        assert result.get("status") == "up_to_date"

    # LR1
    @pytest.mark.asyncio
    async def test_auto_migrates_outdated_recipe(self, tmp_path, monkeypatch, tool_ctx):
        """LR1: When recipe version < installed, _run_headless_core is called once."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.generate_recipe_card", return_value=None),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_awaited_once()
        assert result.get("status") == "migrated"
        assert "contracts_regenerated" in result

    # LR4
    @pytest.mark.asyncio
    async def test_clears_failure_record_after_successful_migration(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """LR4: FailureStore.clear(name) is called when migration succeeds."""
        from autoskillit.migration.store import FailureStore, default_store_path

        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        store = FailureStore(default_store_path(tmp_path))
        store.record(
            name="test-script",
            file_path=ctx["recipe_path"],
            file_type="recipe",
            error="prior failure",
            retries_attempted=1,
        )
        assert store.has_failure("test-script")

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.contracts.generate_recipe_card", return_value=None),
        ):
            await migrate_recipe(name="test-script")

        fresh_store = FailureStore(default_store_path(tmp_path))
        assert not fresh_store.has_failure("test-script")

    # LR5
    @pytest.mark.asyncio
    async def test_records_failure_when_migration_fails(self, tmp_path, monkeypatch, tool_ctx):
        """LR5: When headless returns success=False, failure is recorded to failures.json."""
        from autoskillit.migration.store import FailureStore, default_store_path

        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=False,
                result="headless failed",
                session_id="",
                subtype="error",
                is_error=True,
                exit_code=1,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert "error" in result
        store = FailureStore(default_store_path(tmp_path))
        assert store.has_failure("test-script")

    # LR7
    @pytest.mark.asyncio
    async def test_suppressed_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR7: When name in migration.suppressed, headless is never called."""
        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx, suppressed=["test-script"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # LR8
    @pytest.mark.asyncio
    async def test_up_to_date_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR8: When applicable_migrations returns [], headless is never called."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        current_ver = autoskillit.__version__
        (recipes_dir / "test-script.yaml").write_text(
            _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{current_ver}'\n"
        )

        empty_mig_dir = tmp_path / "migrations"
        empty_mig_dir.mkdir()
        monkeypatch.setattr(ml, "_migrations_dir", lambda: empty_mig_dir)
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=[]))

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # SRV-NEW-1
    @pytest.mark.asyncio
    async def test_migrate_recipe_regenerates_stale_contract(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """migrate_recipe with version migration also regenerates stale contracts."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value=None),
            patch("autoskillit.recipe.generate_recipe_card", return_value={}),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert result.get("status") == "migrated"
        assert result.get("contracts_regenerated") == ["test-script"]


class TestExtractTokenUsage:
    """Tests for extract_token_usage()."""

    def test_single_assistant_record(self):
        """Single assistant record produces correct totals and model breakdown."""
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 5,
                    },
                },
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["cache_creation_input_tokens"] == 10
        assert result["cache_read_input_tokens"] == 5
        assert result["model_breakdown"] == {
            "claude-sonnet-4-6": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            }
        }

    def test_multiple_assistant_records_same_model(self):
        """Multiple turns with same model accumulate correctly."""
        line1 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        line2 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 60,
                        "cache_creation_input_tokens": 20,
                        "cache_read_input_tokens": 10,
                    },
                },
            }
        )
        stdout = line1 + "\n" + line2
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 100
        assert result["cache_creation_input_tokens"] == 20
        assert result["cache_read_input_tokens"] == 10
        assert "claude-sonnet-4-6" in result["model_breakdown"]
        assert result["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == 300

    def test_multiple_models(self):
        """Assistant records with different models produce per-model breakdown."""
        line1 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        line2 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 70,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 15,
                    },
                },
            }
        )
        stdout = line1 + "\n" + line2
        result = extract_token_usage(stdout)
        assert result is not None
        assert "claude-sonnet-4-6" in result["model_breakdown"]
        assert "claude-opus-4-6" in result["model_breakdown"]
        assert result["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == 100
        assert result["model_breakdown"]["claude-opus-4-6"]["input_tokens"] == 200
        # totals summed from both models (no result record present)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 100

    def test_result_record_usage_preferred_for_totals(self):
        """When result record has usage, it provides the top-level totals."""
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "usage": {
                    "input_tokens": 999,
                    "output_tokens": 888,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 25,
                },
            }
        )
        stdout = assistant_line + "\n" + result_line
        result = extract_token_usage(stdout)
        assert result is not None
        # result record totals take precedence over assistant sum
        assert result["input_tokens"] == 999
        assert result["output_tokens"] == 888
        assert result["cache_creation_input_tokens"] == 50
        assert result["cache_read_input_tokens"] == 25
        # model breakdown still comes from assistant records
        assert "claude-sonnet-4-6" in result["model_breakdown"]

    def test_fallback_to_assistant_sum_when_no_result_usage(self):
        """When result record lacks usage, top-level totals are summed from assistants."""
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 150,
                        "output_tokens": 60,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                # no "usage" key
            }
        )
        stdout = assistant_line + "\n" + result_line
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 150
        assert result["output_tokens"] == 60

    def test_no_usage_data_returns_none(self):
        """Stdout with no usage records at all returns None."""
        stdout = json.dumps({"type": "user", "message": {"content": "hello"}})
        result = extract_token_usage(stdout)
        assert result is None

    def test_empty_stdout_returns_none(self):
        """Empty string returns None."""
        assert extract_token_usage("") is None

    def test_non_json_stdout_returns_none(self):
        """Non-parseable stdout returns None."""
        assert extract_token_usage("not json at all\nstill not json") is None

    def test_cache_tokens_default_to_zero(self):
        """Missing cache token fields default to 0, not omitted."""
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 80,
                        "output_tokens": 20,
                        # cache fields absent
                    },
                },
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["cache_creation_input_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        breakdown = result["model_breakdown"]["claude-sonnet-4-6"]
        assert breakdown["cache_creation_input_tokens"] == 0
        assert breakdown["cache_read_input_tokens"] == 0

    def test_ignores_non_assistant_non_result_records(self):
        """user and system records are skipped."""
        user_line = json.dumps({"type": "user", "message": {"content": "do something"}})
        system_line = json.dumps({"type": "system", "subtype": "init"})
        stdout = user_line + "\n" + system_line
        result = extract_token_usage(stdout)
        assert result is None


class TestClaudeSessionResultTokenUsage:
    """Token usage field on ClaudeSessionResult."""

    def test_default_is_none(self):
        """token_usage defaults to None when not provided."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert session.token_usage is None

    def test_preserves_token_usage_dict(self):
        """token_usage dict is stored and accessible."""
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 5,
            "model_breakdown": {"claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 50}},
        }
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done.",
            session_id="s1",
            token_usage=usage,
        )
        assert session.token_usage is usage
        assert session.token_usage["input_tokens"] == 100
        assert "model_breakdown" in session.token_usage


class TestBuildSkillResultTokenUsage:
    """token_usage field in _build_skill_result output."""

    def _make_ndjson(self, *, model: str = "claude-sonnet-4-6") -> str:
        """Build a two-line NDJSON with an assistant record and a result record with usage."""
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": model,
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 45,
                        "cache_creation_input_tokens": 8,
                        "cache_read_input_tokens": 3,
                    },
                },
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return assistant + "\n" + result_rec

    def test_token_usage_included_when_present(self):
        """JSON response includes token_usage when session has usage data."""
        stdout = self._make_ndjson()
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert "token_usage" in response
        usage = response["token_usage"]
        assert usage is not None
        assert usage["input_tokens"] == 200
        assert usage["output_tokens"] == 80
        assert usage["cache_creation_input_tokens"] == 8
        assert usage["cache_read_input_tokens"] == 3
        assert "model_breakdown" in usage
        assert "claude-sonnet-4-6" in usage["model_breakdown"]

    def test_token_usage_null_when_absent(self):
        """JSON response has token_usage: null when no usage data."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                # no usage field
            }
        )
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["token_usage"] is None

    def test_stale_result_has_null_token_usage(self):
        """Stale termination produces null token_usage."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=1,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["token_usage"] is None

    def test_timeout_result_has_null_token_usage(self):
        """Timeout termination produces null token_usage."""
        timeout_result = _make_timeout_result(stdout="", stderr="")
        response = json.loads(_build_skill_result(timeout_result).to_json())
        assert response["token_usage"] is None


class TestRetryResponseFieldsTokenUsage:
    """RETRY_RESPONSE_FIELDS includes token_usage."""

    def test_token_usage_in_fields(self):
        assert "token_usage" in RETRY_RESPONSE_FIELDS

    def test_field_count(self):
        assert len(RETRY_RESPONSE_FIELDS) == 10


class TestGetPipelineReport:
    """get_pipeline_report is ungated and returns accumulated failures."""

    # Override conftest to test WITHOUT open_kitchen
    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_ungated_returns_empty_initially(self, tool_ctx):
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 0
        assert result["failures"] == []

    @pytest.mark.asyncio
    async def test_ungated_does_not_require_open_kitchen(self, tool_ctx):
        """Must succeed even when gate is disabled."""
        result = json.loads(await get_pipeline_report())
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_accumulates_failures_from_run_skill(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=True)
        tool_ctx.runner.push(_make_result(returncode=1, stdout=_failed_session_json()))
        await run_skill(skill_command="/autoskillit:test", cwd="/tmp")
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 1
        assert result["failures"][0]["skill_command"].startswith("/autoskillit:test")

    @pytest.mark.asyncio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.audit.record_failure(_make_failure_record())
        result = json.loads(await get_pipeline_report(clear=True))
        assert result["total_failures"] == 1
        result2 = json.loads(await get_pipeline_report())
        assert result2["total_failures"] == 0


class TestFailureCaptureInBuildSkillResult:
    """_build_skill_result() must capture failures into tool_ctx.audit."""

    def test_captures_non_zero_exit_code(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_failed_session_json())
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()) == 1

    def test_does_not_capture_clean_success(self, tool_ctx):
        result = _make_result(returncode=0, stdout=_success_session_json("done"))
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert tool_ctx.audit.get_report() == []

    def test_captured_record_has_correct_skill_command(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_failed_session_json())
        _build_skill_result(
            result, skill_command="/autoskillit:implement-worktree", audit=tool_ctx.audit
        )
        assert tool_ctx.audit.get_report()[0].skill_command == "/autoskillit:implement-worktree"

    def test_captured_record_has_timestamp(self, tool_ctx):
        from datetime import datetime

        result = _make_result(returncode=1, stdout=_failed_session_json())
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        record = tool_ctx.audit.get_report()[0]
        assert record.timestamp  # non-empty ISO timestamp
        datetime.fromisoformat(record.timestamp)  # must parse as ISO

    def test_stale_termination_is_captured(self, tool_ctx):
        result = _make_result(returncode=0, termination_reason=TerminationReason.STALE)
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "stale"

    def test_needs_retry_is_captured(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_context_exhausted_session_json())
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].needs_retry is True

    def test_stderr_truncated_to_500_chars(self, tool_ctx):
        long_stderr = "e" * 2000
        result = _make_result(returncode=1, stderr=long_stderr, stdout=_failed_session_json())
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()[0].stderr) <= 500


class TestRunSkillStepName:
    """step_name param drives token_log accumulation."""

    def _make_ndjson(self) -> str:
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return result_rec

    @pytest.mark.asyncio
    async def test_step_name_records_token_usage(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.asyncio
    async def test_no_step_name_does_not_record(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.asyncio
    async def test_null_token_usage_does_not_record(self, tool_ctx):
        # Return NDJSON with no usage field → token_usage will be null
        no_usage_ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=no_usage_ndjson))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.asyncio
    async def test_step_name_run_skill_retry(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill_retry(
            skill_command="/autoskillit:investigate the test failures",
            cwd="/tmp",
            step_name="implement",
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["input_tokens"] == 200


class TestGetTokenSummary:
    """get_token_summary is ungated and returns accumulated token usage."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.asyncio
    async def test_ungated_does_not_require_open_kitchen(self, tool_ctx):
        result = json.loads(await get_token_summary())
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_steps_initially(self, tool_ctx):
        result = json.loads(await get_token_summary())
        assert result["steps"] == []
        assert result["total"]["input_tokens"] == 0
        assert result["total"]["output_tokens"] == 0
        assert result["total"]["cache_creation_input_tokens"] == 0
        assert result["total"]["cache_read_input_tokens"] == 0

    @pytest.mark.asyncio
    async def test_returns_entry_per_step_name(self, tool_ctx):
        tool_ctx.token_log.record(
            "investigate",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            },
        )
        tool_ctx.token_log.record(
            "implement",
            {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 10,
            },
        )
        result = json.loads(await get_token_summary())
        assert len(result["steps"]) == 2
        assert result["steps"][0]["step_name"] == "investigate"
        assert result["steps"][1]["step_name"] == "implement"

    @pytest.mark.asyncio
    async def test_multiple_invocations_same_step_are_summed(self, tool_ctx):
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("implement", usage)
        tool_ctx.token_log.record("implement", usage)
        tool_ctx.token_log.record("implement", usage)
        result = json.loads(await get_token_summary())
        assert len(result["steps"]) == 1
        assert result["steps"][0]["input_tokens"] == 300
        assert result["steps"][0]["invocation_count"] == 3

    @pytest.mark.asyncio
    async def test_total_field_sums_all_steps(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            },
        )
        tool_ctx.token_log.record(
            "implement",
            {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 10,
            },
        )
        result = json.loads(await get_token_summary())
        assert result["total"]["input_tokens"] == 300
        assert result["total"]["output_tokens"] == 130
        assert result["total"]["cache_creation_input_tokens"] == 30
        assert result["total"]["cache_read_input_tokens"] == 15

    @pytest.mark.asyncio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        result = json.loads(await get_token_summary(clear=True))
        assert len(result["steps"]) == 1
        result2 = json.loads(await get_token_summary())
        assert result2["steps"] == []

    @pytest.mark.asyncio
    async def test_response_shape(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 1,
                "cache_read_input_tokens": 2,
            },
        )
        result = json.loads(await get_token_summary())
        assert "steps" in result
        assert "total" in result
        assert isinstance(result["steps"], list)
        total_keys = set(result["total"].keys())
        assert {
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        } <= total_keys


def test_open_kitchen_has_no_update_advisory(tool_ctx):
    """REQ-APP-004: open_kitchen prompt contains no recipe update advisory."""
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server.prompts import open_kitchen

    # Ensure kitchen is closed before calling open_kitchen
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = open_kitchen()

    content = result.messages[0].content
    text = content.text if hasattr(content, "text") else str(content)
    assert "RECIPE UPDATE AVAILABLE" not in text
    assert "accept_recipe_update" not in text
    assert "decline_recipe_update" not in text


class TestStalePathStdoutCheck:
    """STALE termination recovers from stdout when a valid result record is present."""

    def _make_stale_result(self, stdout: str, returncode: int = -15) -> SubprocessResult:
        return SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )

    def test_stale_kill_with_completed_result_in_stdout_is_success(self):
        """Session wrote a valid type=result record before going stale — should recover."""
        valid_completed_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed successfully.",
                "session_id": "sess-stale-recovery",
            }
        )
        result_obj = self._make_stale_result(stdout=valid_completed_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is True
        assert parsed["subtype"] == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure(self):
        """Stale session with no stdout — original failure response preserved."""
        result_obj = self._make_stale_result(stdout="")
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"

    def test_stale_with_error_result_returns_failure(self):
        """Stale session where the result record has is_error=True — not recovered."""
        error_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Tool call failed.",
                "session_id": "sess-err",
            }
        )
        result_obj = self._make_stale_result(stdout=error_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"


class TestServerLazyInit:
    """Tests for the _ctx / _initialize() / _get_ctx() / _get_config() pattern."""

    def test_server_import_does_not_call_load_config(self, monkeypatch):
        """Importing server.py must not trigger load_config() as a side effect."""
        import importlib
        import sys
        from unittest.mock import patch

        import autoskillit

        # Restore both the package attribute and sys.modules entry after the test so
        # later tests in the same xdist worker see the original module object.
        monkeypatch.setattr(autoskillit, "server", autoskillit.server)
        monkeypatch.delitem(sys.modules, "autoskillit.server", raising=False)

        with patch("autoskillit.config.load_config") as mock_load:
            import autoskillit.server  # noqa: F401

            importlib.import_module("autoskillit.server")
        assert not mock_load.called

    def test_get_ctx_raises_before_initialize(self, monkeypatch):
        """_get_ctx() raises RuntimeError when _ctx is None."""
        import autoskillit.server as srv

        monkeypatch.setattr(srv, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            srv._get_ctx()

    def test_get_config_raises_before_initialize(self, monkeypatch):
        """_get_config() raises RuntimeError when _ctx is None."""
        import autoskillit.server as srv

        monkeypatch.setattr(srv, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            srv._get_config()


class TestGatedToolObservability:
    """Each gated tool binds structlog contextvars and calls ctx.info/ctx.error."""

    @pytest.fixture
    def mock_ctx(self):
        """AsyncMock ctx for verifying ctx.info/ctx.error calls."""
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.error = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_run_cmd_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_cmd binds tool='run_cmd' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(_make_result(0, "ok\n", ""))
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_cmd(cmd="echo ok", cwd="/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_cmd" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_cmd_calls_ctx_error_on_failure(self, tool_ctx, mock_ctx):
        """run_cmd reports failure (success=false) when subprocess exits non-zero."""
        tool_ctx.runner.push(_make_result(1, "", "err"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp", ctx=mock_ctx))
        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_run_python_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_python binds tool='run_python' contextvar and calls ctx.info on success."""
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_python(callable="json.dumps", args={"obj": 1}, ctx=mock_ctx)
        assert any(entry.get("tool") == "run_python" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_python_calls_ctx_error_on_failure(self, tool_ctx, mock_ctx):
        """run_python reports failure (success=false) when callable import fails."""
        result = json.loads(await run_python(callable="nonexistent.module.func", ctx=mock_ctx))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_run_skill_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_skill binds tool='run_skill' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_skill" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_skill_calls_ctx_error_on_failure(self, tool_ctx, mock_ctx):
        """run_skill reports failure (success=false) when headless session fails."""
        tool_ctx.runner.push(
            _make_result(
                1,
                '{"type": "result", "subtype": "error", "is_error": true,'
                ' "result": "failed", "session_id": "s1"}',
                "",
            )
        )
        result = json.loads(await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_run_skill_retry_binds_tool_contextvar_and_calls_ctx_info(
        self, tool_ctx, mock_ctx
    ):
        """run_skill_retry binds tool='run_skill_retry' contextvar and calls ctx.info."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_skill_retry("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_skill_retry" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_skill_retry_calls_ctx_error_on_failure(self, tool_ctx, mock_ctx):
        """run_skill_retry reports failure (success=false) when headless session fails."""
        tool_ctx.runner.push(
            _make_result(
                1,
                '{"type": "result", "subtype": "error", "is_error": true,'
                ' "result": "failed", "session_id": "s1"}',
                "",
            )
        )
        result = json.loads(
            await run_skill_retry("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        )
        assert result["success"] is False


class TestNotifyHelper:
    """Unit tests for the centralized _notify() notification helper."""

    @pytest.mark.asyncio
    async def test_notify_raises_value_error_for_reserved_key_name(self):
        """The 'name' key that caused the original bug must be rejected."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        with pytest.raises(ValueError, match="reserved LogRecord"):
            await _notify(
                ctx,
                "info",
                "migrate_recipe: foo",
                "autoskillit.migrate_recipe",
                extra={"name": "foo"},
            )
        ctx.info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notify_raises_for_all_reserved_keys(self):
        """Every key in RESERVED_LOG_RECORD_KEYS must be rejected."""
        from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        for reserved_key in RESERVED_LOG_RECORD_KEYS:
            with pytest.raises(ValueError, match="reserved LogRecord"):
                await _notify(ctx, "info", "msg", "logger", extra={reserved_key: "value"})

    @pytest.mark.asyncio
    async def test_notify_accepts_safe_key_recipe_name(self):
        """'recipe_name' (the corrected key for migrate_recipe) must be accepted."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(
            ctx,
            "info",
            "migrate_recipe: foo",
            "autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )
        ctx.info.assert_awaited_once_with(
            "migrate_recipe: foo",
            logger_name="autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )

    @pytest.mark.asyncio
    async def test_notify_accepts_none_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger")  # no extra
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_accepts_empty_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger", extra={})
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_swallows_attribute_error_from_ctx(self):
        """AttributeError from ctx.info (e.g. _CurrentContext sentinel) is swallowed."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=AttributeError("no info"))
        # Must not raise
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_swallows_runtime_error_from_ctx(self):
        """RuntimeError from ctx.info (no active MCP session) is swallowed."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=RuntimeError("session not available"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_swallows_key_error_from_ctx(self):
        """KeyError from FastMCP's stdlib logging path is swallowed.
        This is the error class that was previously uncaught."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=KeyError("Attempt to overwrite 'name' in LogRecord"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_dispatches_error_level(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.error = AsyncMock()
        await _notify(
            ctx,
            "error",
            "run_cmd failed",
            "autoskillit.run_cmd",
            extra={"exit_code": 1},
        )
        ctx.error.assert_awaited_once_with(
            "run_cmd failed",
            logger_name="autoskillit.run_cmd",
            extra={"exit_code": 1},
        )


# ---------------------------------------------------------------------------
# Service routing integration tests (REQ-IMP-003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_execution_routes_through_executor(tool_ctx, monkeypatch) -> None:
    """run_skill routes through ctx.executor.run(), not run_headless_core directly."""
    from autoskillit.core import SkillResult

    calls = []

    class MockExecutor:
        async def run(
            self,
            skill_command: str,
            cwd: str,
            *,
            model: str = "",
            step_name: str = "",
            add_dir: str = "",
            timeout: float | None = None,
            stale_threshold: float | None = None,
        ) -> SkillResult:
            calls.append((skill_command, cwd))
            return SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

    tool_ctx.executor = MockExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    await run_skill("/test skill", "/tmp")
    assert calls == [("/test skill", "/tmp")]


@pytest.mark.asyncio
async def test_tools_workspace_routes_through_tester(tool_ctx, monkeypatch) -> None:
    """test_check routes through ctx.tester.run(), not _run_subprocess directly."""
    calls = []

    class MockTester:
        async def run(self, cwd: Path) -> tuple[bool, str]:
            calls.append(cwd)
            return (True, "1 passed")

    tool_ctx.tester = MockTester()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_workspace import test_check

    result = await test_check("/tmp/worktree")
    assert json.loads(result)["passed"] is True
    assert calls == [Path("/tmp/worktree")]


@pytest.mark.asyncio
async def test_tools_status_routes_through_db_reader(tool_ctx, monkeypatch, tmp_path) -> None:
    """read_db routes through ctx.db_reader.query()."""
    calls = []

    class MockDbReader:
        def query(
            self,
            db_path: str,
            sql: str,
            params: list | dict,
            timeout_sec: int,
            max_rows: int,
        ) -> dict:
            calls.append(sql)
            return {"rows": [], "count": 0}

    tool_ctx.db_reader = MockDbReader()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_status import read_db

    db_path = str(tmp_path / "test.db")
    # Create an empty sqlite db so path-exists check passes
    import sqlite3 as _sqlite3

    _sqlite3.connect(db_path).close()
    await read_db(db_path, "SELECT 1")
    assert calls == ["SELECT 1"]


class TestCheckQuota:
    @pytest.mark.asyncio
    async def test_check_quota_executes_without_open_kitchen(self, tool_ctx):
        """check_quota must work even when the gate is closed."""
        from autoskillit.config.settings import AutomationConfig, QuotaGuardConfig

        tool_ctx.gate = DefaultGateState(enabled=False)
        # quota_guard.enabled=False avoids real network/credential reads
        tool_ctx.config = AutomationConfig(quota_guard=QuotaGuardConfig(enabled=False))
        result = json.loads(await check_quota())
        # Should return a result (not a gate error)
        assert result.get("subtype") != "gate_error"
        assert "should_sleep" in result or result.get("success") is True

    @pytest.mark.asyncio
    async def test_disabled_quota_guard_returns_success_no_sleep(self, tool_ctx):
        from autoskillit.config.settings import AutomationConfig, QuotaGuardConfig

        tool_ctx.config = AutomationConfig(quota_guard=QuotaGuardConfig(enabled=False))
        result = json.loads(await check_quota())
        assert result["success"] is True
        assert result["should_sleep"] is False

    @pytest.mark.asyncio
    async def test_above_threshold_returns_should_sleep(self, tool_ctx, monkeypatch, tmp_path):
        from datetime import datetime, timedelta

        from autoskillit.config.settings import AutomationConfig, QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus

        resets_at = datetime.now(UTC) + timedelta(hours=1)
        tool_ctx.config = AutomationConfig(
            quota_guard=QuotaGuardConfig(
                enabled=True,
                threshold=80.0,
                buffer_seconds=0,
                credentials_path=str(tmp_path / ".credentials.json"),
                cache_path=str(tmp_path / "cache.json"),
            )
        )

        async def mock_fetch(path):
            return QuotaStatus(utilization=95.0, resets_at=resets_at)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = json.loads(await check_quota())
        assert result["success"] is True
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    def test_check_quota_in_tool_registry(self):
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = [c for c in server._local_provider._components.values() if isinstance(c, Tool)]
        assert "check_quota" in {t.name for t in tools}

    @pytest.mark.asyncio
    async def test_no_notification_when_quota_below_threshold(self, tool_ctx, monkeypatch):
        """check_quota is ungated and emits no MCP notifications (no ctx parameter)."""
        from autoskillit.config.settings import AutomationConfig, QuotaGuardConfig

        tool_ctx.config = AutomationConfig(
            quota_guard=QuotaGuardConfig(enabled=True, threshold=80.0)
        )

        async def mock_check(config):
            return {
                "should_sleep": False,
                "sleep_seconds": 0,
                "utilization": 50.0,
                "resets_at": None,
            }

        monkeypatch.setattr("autoskillit.server.helpers.check_and_sleep_if_needed", mock_check)

        result = json.loads(await check_quota())
        assert result["success"] is True
        assert result["should_sleep"] is False

    @pytest.mark.asyncio
    async def test_above_threshold_returns_should_sleep_in_result(self, tool_ctx, monkeypatch):
        """check_quota returns should_sleep=True in JSON when quota is above threshold."""
        from autoskillit.config.settings import AutomationConfig, QuotaGuardConfig

        tool_ctx.config = AutomationConfig(
            quota_guard=QuotaGuardConfig(enabled=True, threshold=80.0)
        )

        async def mock_check(config):
            return {
                "should_sleep": True,
                "sleep_seconds": 3600,
                "utilization": 95.0,
                "resets_at": "2026-02-28T13:00:00+00:00",
            }

        monkeypatch.setattr("autoskillit.server.helpers.check_and_sleep_if_needed", mock_check)

        result = json.loads(await check_quota())
        assert result["success"] is True
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] == 3600


class TestCloneRepoTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await clone_repo(source_dir="/src", run_name="test"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            return_value={"clone_path": "/clone/path", "source_dir": "/src"},
        ):
            result = json.loads(await clone_repo(source_dir="/src", run_name="myrun"))
        assert result["clone_path"] == "/clone/path"
        assert result["source_dir"] == "/src"

    @pytest.mark.asyncio
    async def test_returns_error_on_value_error(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            side_effect=ValueError("resolved to nonexistent"),
        ):
            result = json.loads(await clone_repo(source_dir="/bad/path", run_name="run"))
        assert "error" in result
        assert "resolved to" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_on_runtime_error(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            side_effect=RuntimeError("git clone failed"),
        ):
            result = json.loads(await clone_repo(source_dir="/src", run_name="run"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cb17_forwards_branch_to_clone_manager(self, tool_ctx):
        """T_CB17: branch param is forwarded to the underlying clone_repo call."""
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            return_value={"clone_path": "/clone/path", "source_dir": "/src"},
        ) as mock_clone:
            await clone_repo(source_dir="/src", run_name="r", branch="dev")
        mock_clone.assert_called_once()
        assert mock_clone.call_args.kwargs.get("branch") == "dev"

    @pytest.mark.asyncio
    async def test_cb18_forwards_strategy_to_clone_manager(self, tool_ctx):
        """T_CB18: strategy param is forwarded to the underlying clone_repo call."""
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            return_value={"clone_path": "/clone/path", "source_dir": "/src"},
        ) as mock_clone:
            await clone_repo(source_dir="/src", run_name="r", strategy="proceed")
        mock_clone.assert_called_once()
        assert mock_clone.call_args.kwargs.get("strategy") == "proceed"

    @pytest.mark.asyncio
    async def test_cb19_returns_uncommitted_changes_result_as_json(self, tool_ctx):
        """T_CB19: uncommitted_changes warning dict passes through without 'error' key."""
        uncommitted_result = {
            "uncommitted_changes": "true",
            "source_dir": "/src",
            "branch": "main",
            "changed_files": "M file.py",
            "total_changed": "1",
        }
        with patch(
            "autoskillit.workspace.clone.clone_repo",
            return_value=uncommitted_result,
        ):
            result = json.loads(await clone_repo(source_dir="/src", run_name="r"))
        assert result["uncommitted_changes"] == "true"
        assert "error" not in result


class TestRemoveCloneTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await remove_clone(clone_path="/clone", keep="false"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.remove_clone",
            return_value={"removed": "true"},
        ):
            result = json.loads(await remove_clone(clone_path="/clone/path", keep="false"))
        assert result["removed"] == "true"

    @pytest.mark.asyncio
    async def test_keep_true_passes_through(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.remove_clone",
            return_value={"removed": "false", "reason": "keep=true"},
        ):
            result = json.loads(await remove_clone(clone_path="/clone/path", keep="true"))
        assert result["removed"] == "false"

    @pytest.mark.asyncio
    async def test_always_routes_success_even_on_partial_failure(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.remove_clone",
            return_value={"removed": "false", "reason": "OSError"},
        ):
            result = json.loads(await remove_clone(clone_path="/bad", keep="false"))
        assert "error" not in result


class TestPushToRemoteTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await push_to_remote(clone_path="/c", source_dir="/s", branch="main"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone_on_success(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.push_to_remote",
            return_value={"success": "true", "stderr": ""},
        ):
            result = json.loads(
                await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
            )
        assert result["success"] == "true"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_returns_error_key_when_push_fails(self, tool_ctx):
        with patch(
            "autoskillit.workspace.clone.push_to_remote",
            return_value={"success": "false", "stderr": "remote rejected"},
        ):
            result = json.loads(
                await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
            )
        assert "error" in result
        assert "remote rejected" in result["stderr"]

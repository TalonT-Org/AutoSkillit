"""Tests for autoskillit server MCP tools."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.config import (
    AutomationConfig,
    ClassifyFixConfig,
    ReadDbConfig,
    ResetWorkspaceConfig,
    RunSkillConfig,
    SafetyConfig,
)
from autoskillit.process_lifecycle import SubprocessResult
from autoskillit.server import (
    ClaudeSessionResult,
    CleanupResult,
    _build_skill_result,
    _check_dry_walkthrough,
    _compute_success,
    _delete_directory_contents,
    _disable_tools_handler,
    _enable_tools_handler,
    _ensure_skill_prefix,
    _gate_error_result,
    _parse_pytest_summary,
    _require_enabled,
    _run_subprocess,
    _select_only_authorizer,
    _session_log_dir,
    _validate_select_only,
    autoskillit_status,
    classify_fix,
    list_skill_scripts,
    load_skill_script,
    merge_worktree,
    parse_session_result,
    read_db,
    reset_test_dir,
    reset_workspace,
    run_cmd,
    run_python,
    run_skill,
    run_skill_retry,
    test_check,
    validate_script,
)
from autoskillit.types import (
    CONTEXT_EXHAUSTION_MARKER,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
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
    @patch("autoskillit.server.run_managed_async")
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
    @patch("autoskillit.server.run_managed_async")
    async def test_failing_command(self, mock_run):
        mock_run.return_value = _make_result(1, "", "error")
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp"))

        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_custom_timeout(self, mock_run):
        mock_run.return_value = _make_result(0, "", "")
        await run_cmd(cmd="sleep 1", cwd="/tmp", timeout=30)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 30.0


class TestRunSkillPluginDir:
    """T2: run_skill and run_skill_retry pass --plugin-dir to the claude command."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_passes_plugin_dir(self, mock_run):
        """run_skill includes --plugin-dir and the package path in the command."""
        import autoskillit
        from autoskillit import server

        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate some-error", "/tmp")

        cmd = mock_run.call_args[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == server._plugin_dir
        assert server._plugin_dir == str(Path(autoskillit.__file__).parent)

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_retry_passes_plugin_dir(self, mock_run):
        """run_skill_retry includes --plugin-dir and the package path in the command."""
        import autoskillit
        from autoskillit import server

        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill_retry("/investigate some-error", "/tmp")

        cmd = mock_run.call_args[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == server._plugin_dir
        assert server._plugin_dir == str(Path(autoskillit.__file__).parent)


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
    """T6: _plugin_dir points to the package root directory."""

    def test_plugin_dir_returns_package_root(self):
        """_plugin_dir equals the autoskillit package directory."""
        import autoskillit
        from autoskillit.server import _plugin_dir

        assert _plugin_dir == str(Path(autoskillit.__file__).parent)


class TestVersionInfo:
    """_version_info() returns package and plugin.json versions."""

    def test_version_info_returns_package_and_plugin_versions(self):
        from autoskillit import __version__
        from autoskillit.server import _version_info

        info = _version_info()
        assert isinstance(info["package_version"], str)
        assert isinstance(info["plugin_json_version"], str)
        assert info["package_version"] == __version__
        assert info["match"] is True

    def test_version_info_detects_mismatch(self, tmp_path, monkeypatch):
        from autoskillit import server
        from autoskillit.server import _version_info

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path))
        info = _version_info()
        assert info["match"] is False
        assert info["package_version"] != info["plugin_json_version"]
        assert info["plugin_json_version"] == "0.0.0"

    def test_version_info_handles_missing_plugin_json(self, tmp_path, monkeypatch):
        from autoskillit import server
        from autoskillit.server import _version_info

        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path))
        info = _version_info()
        assert info["plugin_json_version"] is None
        assert info["match"] is False


class TestClassifyFix:
    """T4, T5: classify_fix returns correct restart scope based on changed files."""

    @pytest.fixture(autouse=True)
    def _set_prefixes(self, monkeypatch):
        """Configure critical path prefixes for classify_fix tests."""
        from autoskillit import server

        cfg = AutomationConfig(
            classify_fix=ClassifyFixConfig(
                path_prefixes=[
                    "src/core/",
                    "src/api/",
                    "lib/handlers/",
                ]
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_critical_files_return_full_restart(self, mock_run):
        changed = "src/core/handler.py\nlib/utils/helpers.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert len(result["critical_files"]) == 1
        assert result["critical_files"][0] == "src/core/handler.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_non_critical_returns_partial_restart(self, mock_run):
        changed = "src/workers/runner.py\nlib/utils/helpers.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["critical_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_git_diff_failure(self, mock_run):
        mock_run.return_value = _make_result(128, "", "fatal: bad revision")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert "error" in result
        assert "git diff failed" in result["error"]

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_critical_path_in_diff_triggers_full_restart(self, mock_run):
        changed = "src/api/routes.py\n"
        mock_run.return_value = _make_result(0, changed, "")

        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART


class TestResetWorkspace:
    """T6, T7: reset_workspace preserves configured dirs, requires marker."""

    @pytest.fixture(autouse=True)
    def _set_reset_command(self, monkeypatch):
        """Configure reset_workspace with a command for these tests."""
        from autoskillit import server

        cfg = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["make", "clean"],
                preserve_dirs={".cache", "reports"},
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

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
    @patch("autoskillit.server.run_managed_async")
    async def test_preserves_configured_dirs(self, mock_run, tmp_path):
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

        mock_run.return_value = _make_result(0, "", "")

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
    @patch("autoskillit.server.run_managed_async")
    async def test_reset_command_failure(self, mock_run, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        mock_run.return_value = _make_result(1, "", "command not found")

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "error" in result
        assert result["error"] == "reset command failed"
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_runs_correct_reset_command(self, mock_run, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        mock_run.return_value = _make_result(0, "", "")

        await reset_workspace(test_dir=str(workspace))

        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "make",
            "clean",
        ]


class TestCheckDryWalkthrough:
    """Dry-walkthrough gate blocks both /autoskillit:implement-worktree variants."""

    def test_dry_walkthrough_gate_blocks_implement_no_merge(self, tmp_path):
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

    def test_dry_walkthrough_gate_passes_implement_no_merge(self, tmp_path):
        """Gate allows /autoskillit:implement-worktree-no-merge when plan has marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# My Plan")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is None

    def test_dry_walkthrough_gate_still_works_for_implement_worktree(self, tmp_path):
        """Original /autoskillit:implement-worktree gating is not broken."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True

    def test_dry_walkthrough_gate_ignores_unrelated_skills(self):
        """Gate ignores skills that are not implement-worktree variants."""
        result = _check_dry_walkthrough("/autoskillit:investigate some-error", "/tmp")
        assert result is None


class TestMergeWorktree:
    """merge_worktree enforces test gate, rebases, and merges."""

    @pytest.mark.asyncio
    @patch("autoskillit.server._run_subprocess")
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
        assert result["failed_step"] == MergeFailedStep.TEST_GATE
        assert result["state"] == MergeState.WORKTREE_INTACT
        assert "test_summary" not in result

    @pytest.mark.asyncio
    @patch("autoskillit.server._run_subprocess")
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
    @patch("autoskillit.server._run_subprocess")
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
        assert result["failed_step"] == MergeFailedStep.REBASE
        assert result["state"] == MergeState.WORKTREE_INTACT_REBASE_ABORTED

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
    """All 14 tools are registered on the MCP server."""

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
            "list_skill_scripts",
            "load_skill_script",
            "autoskillit_status",
            "validate_script",
        }
        assert expected == tool_names


class TestAutoskillitStatus:
    """autoskillit_status tool returns version health info (ungated)."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self, monkeypatch):
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

    @pytest.mark.asyncio
    async def test_status_returns_version_info(self):
        from autoskillit import __version__

        result = json.loads(await autoskillit_status())
        assert result["package_version"] == __version__
        assert result["plugin_json_version"] == __version__
        assert result["versions_match"] is True
        assert "warning" not in result

    @pytest.mark.asyncio
    async def test_status_reports_mismatch(self, tmp_path, monkeypatch):
        from autoskillit import server

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path))
        result = json.loads(await autoskillit_status())
        assert result["versions_match"] is False
        assert "warning" in result
        assert "mismatch" in result["warning"].lower()

    @pytest.mark.asyncio
    async def test_status_works_without_enable(self):
        from autoskillit import server

        assert server._tools_enabled is False
        result = json.loads(await autoskillit_status())
        assert result["tools_enabled"] is False
        assert "package_version" in result


class TestSkillScriptTools:
    """Tests for ungated list_skill_scripts and load_skill_script tools."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self, monkeypatch):
        """Verify these tools work WITHOUT tool activation."""
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

    # SS1
    @pytest.mark.asyncio
    @patch("autoskillit.script_loader.list_scripts")
    async def test_list_returns_json_object(self, mock_list):
        """list_skill_scripts returns JSON object with scripts array (not gated)."""
        from autoskillit.script_loader import ScriptInfo
        from autoskillit.types import LoadResult

        mock_list.return_value = LoadResult(
            items=[
                ScriptInfo(
                    name="impl", description="Implement", summary="plan > impl", path=Path("/x")
                ),
            ],
            errors=[],
        )
        result = json.loads(await list_skill_scripts())
        assert isinstance(result, dict)
        assert len(result["scripts"]) == 1
        assert result["scripts"][0]["name"] == "impl"
        assert result["scripts"][0]["description"] == "Implement"
        assert result["scripts"][0]["summary"] == "plan > impl"
        assert "errors" not in result

    # SS2
    @pytest.mark.asyncio
    @patch("autoskillit.script_loader.load_script")
    async def test_load_returns_raw_yaml(self, mock_load):
        """load_skill_script returns raw YAML content (not gated)."""
        mock_load.return_value = "name: test\ndescription: Test script\n"
        result = await load_skill_script(name="test")
        assert "name: test" in result
        assert "description: Test script" in result

    # SS3
    @pytest.mark.asyncio
    @patch("autoskillit.script_loader.load_script")
    async def test_load_unknown_returns_error(self, mock_load):
        """load_skill_script returns error JSON for unknown script name."""
        mock_load.return_value = None
        result = json.loads(await load_skill_script(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    # SS4
    @pytest.mark.asyncio
    @patch("autoskillit.script_loader.list_scripts")
    async def test_list_reports_errors_in_response(self, mock_list):
        """list_skill_scripts includes errors in JSON when scripts fail to parse."""
        from autoskillit.types import LoadReport, LoadResult

        mock_list.return_value = LoadResult(
            items=[],
            errors=[LoadReport(path=Path("/scripts/broken.yaml"), error="bad yaml")],
        )
        result = json.loads(await list_skill_scripts())
        assert "errors" in result
        assert len(result["errors"]) == 1
        assert result["errors"][0]["file"] == "broken.yaml"
        assert "bad yaml" in result["errors"][0]["error"]

    # SS5
    @pytest.mark.asyncio
    async def test_list_integration_discovers_frontmatter(self, tmp_path, monkeypatch):
        """Server tool discovers scripts even when body has YAML-like syntax."""
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "pipeline.yaml").write_text(
            "---\nname: test-pipe\ndescription: Test\nsummary: a > b\n---\n\n"
            "# Pipeline\n\nSETUP:\n  - project_dir = /path/to/project\n"
        )
        result = json.loads(await list_skill_scripts())
        assert len(result["scripts"]) == 1
        assert result["scripts"][0]["name"] == "test-pipe"

    # SS6
    @pytest.mark.asyncio
    async def test_list_integration_reports_errors(self, tmp_path, monkeypatch):
        """Server tool reports parse errors to the caller from real files."""
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "broken.yaml").write_text(":: bad yaml {{[\n")
        result = json.loads(await list_skill_scripts())
        assert "errors" in result
        assert len(result["errors"]) == 1


class TestValidateScript:
    """Tests for ungated validate_script tool."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self, monkeypatch):
        """Verify this tool works WITHOUT tool activation."""
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

    # VS1
    @pytest.mark.asyncio
    async def test_valid_script_returns_success(self, tmp_path):
        """validate_script returns valid=true for a correct script."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: test\n"
            "description: A test script\n"
            "summary: a > b\n"
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
        result = json.loads(await validate_script(script_path=str(script)))
        assert result["valid"] is True
        assert result["errors"] == []

    # VS2
    @pytest.mark.asyncio
    async def test_invalid_script_returns_errors(self, tmp_path):
        """validate_script returns valid=false with errors for missing name."""
        script = tmp_path / "bad.yaml"
        script.write_text("description: Missing name\nsteps:\n  do_thing:\n    tool: run_cmd\n")
        result = json.loads(await validate_script(script_path=str(script)))
        assert result["valid"] is False
        assert any("name" in e for e in result["errors"])

    # VS3
    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error(self):
        """validate_script returns error for nonexistent file."""
        result = json.loads(await validate_script(script_path="/nonexistent/path.yaml"))
        assert "error" in result
        assert "not found" in result["error"].lower() or "File not found" in result["error"]

    # VS4
    @pytest.mark.asyncio
    async def test_malformed_yaml_returns_error(self, tmp_path):
        """validate_script returns error for unparseable YAML."""
        script = tmp_path / "broken.yaml"
        script.write_text("key: [\n  unclosed\n")
        result = json.loads(await validate_script(script_path=str(script)))
        assert "error" in result
        assert "yaml" in result["error"].lower() or "YAML" in result["error"]

    # T_OR10
    @pytest.mark.asyncio
    async def test_validate_script_with_on_result(self, tmp_path):
        """validate_script correctly validates on_result blocks."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: result-script\n"
            "description: Uses on_result\n"
            "steps:\n"
            "  classify:\n"
            "    tool: classify_fix\n"
            "    on_result:\n"
            "      field: restart_scope\n"
            "      routes:\n"
            "        full_restart: done\n"
            "        partial_restart: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_script(script_path=str(script)))
        assert result["valid"] is True


class TestToolSchemas:
    """Regression guard: tool descriptions must not contain legacy terminology."""

    FORBIDDEN_TERMS = {
        "executor",
        "planner",
        "bugfix-loop",
        "automation-mcp",
        "ai-executor",
    }

    REQUIRED_CROSS_REFS: dict[str, list[str]] = {
        "list_skill_scripts": [
            "make-script-skill",
        ],
        "load_skill_script": [
            "make-script-skill",
        ],
        "validate_script": [
            "make-script-skill",
        ],
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

    def test_script_tools_have_disambiguation(self):
        """All script-related tools must carry the 'NOT slash commands' disclaimer."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        script_tools = ["list_skill_scripts", "load_skill_script", "validate_script"]
        for tool_name in script_tools:
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            desc = tool.description or ""
            assert "NOT slash commands" in desc, (
                f"Tool '{tool_name}' must contain 'NOT slash commands' disclaimer"
            )


class TestResetGuard:
    """Marker-file-based reset guard for destructive operations."""

    @pytest.mark.asyncio
    async def test_reset_test_dir_refuses_without_marker(self, tmp_path):
        """Directory without marker file is refused."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower() or "reset guard" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reset_test_dir_allows_with_marker(self, tmp_path):
        """Directory with marker file is cleared."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert not (target / "some_file.txt").exists()

    @pytest.mark.asyncio
    async def test_reset_test_dir_preserves_marker(self, tmp_path):
        """Reset preserves the marker file so the workspace is reusable."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "data.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert (target / ".autoskillit-workspace").is_file()

    @pytest.mark.asyncio
    async def test_reset_workspace_refuses_without_marker(self, monkeypatch, tmp_path):
        """reset_workspace also checks for marker."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        monkeypatch.setattr(server, "_config", cfg)
        target = tmp_path / "workspace"
        target.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert "error" in result

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_reset_workspace_allows_with_marker(self, mock_run, monkeypatch, tmp_path):
        """reset_workspace clears when marker is present."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        monkeypatch.setattr(server, "_config", cfg)
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "file.txt").touch()
        mock_run.return_value = _make_result(0, "", "")
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_custom_marker_name(self, monkeypatch, tmp_path):
        """Config can override marker file name."""
        from autoskillit import server

        cfg = AutomationConfig(safety=SafetyConfig(reset_guard_marker=".my-workspace"))
        monkeypatch.setattr(server, "_config", cfg)
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".my-workspace").touch()
        (target / "file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_force_overrides_marker_check(self, tmp_path):
        """force=True on reset_test_dir bypasses marker requirement."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "file.txt").touch()
        # No marker, but force=True
        result = json.loads(await reset_test_dir(test_dir=str(target), force=True))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_rejects_nonexistent(self, tmp_path):
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
        assert cfg.test_check.command == ["task", "test-all"]

    def test_default_classify_fix_empty_prefixes(self):
        cfg = AutomationConfig()
        assert cfg.classify_fix.path_prefixes == []


class TestRunSubprocessDelegatesToManaged:
    """Verify _run_subprocess delegates to run_managed_async correctly."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_normal_completion(self, mock_run):
        mock_run.return_value = _make_result(0, "output", "")
        rc, stdout, stderr = await _run_subprocess(["echo", "hi"], cwd="/tmp", timeout=10)
        assert rc == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_timeout_returns_minus_one(self, mock_run):
        mock_run.return_value = _make_timeout_result()
        rc, stdout, stderr = await _run_subprocess(["sleep", "999"], cwd="/tmp", timeout=1)
        assert rc == -1
        assert "timed out" in stderr


class TestTestCheck:
    """test_check returns unambiguous PASS/FAIL with cross-validation."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_passes_on_clean_run(self, mock_run):
        """returncode=0 with passing summary -> passed=True."""
        mock_run.return_value = _make_result(0, "100 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_fails_on_nonzero_exit(self, mock_run):
        """returncode=1 -> passed=False regardless of output."""
        mock_run.return_value = _make_result(1, "3 failed, 97 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_cross_validates_exit_code_against_output(self, mock_run):
        """returncode=0 but output contains 'failed' -> passed=False.
        This is THE bug: Taskfile PIPESTATUS fails silently, exit code is 0,
        but output clearly shows test failures."""
        mock_run.return_value = _make_result(0, "3 failed, 8538 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
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
    @patch("autoskillit.server.run_managed_async")
    async def test_cross_validates_error_in_output(self, mock_run):
        """returncode=0 but output contains 'error' -> passed=False."""
        mock_run.return_value = _make_result(0, "1 error, 99 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_xfailed_not_treated_as_failure(self, mock_run):
        """xfailed tests are expected failures — exit code 0, should pass."""
        mock_run.return_value = _make_result(0, "8552 passed, 3 xfailed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_xpassed_not_treated_as_failure(self, mock_run):
        """xpassed tests are unexpected passes — exit code 0, should pass."""
        mock_run.return_value = _make_result(0, "99 passed, 1 xpassed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_mixed_xfail_with_real_failure(self, mock_run):
        """Real failure + xfailed — should still fail on the real failure."""
        mock_run.return_value = _make_result(0, "1 failed, 2 xfailed, 97 passed\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_skipped_with_exit_zero_passes(self, mock_run):
        """Skipped tests with exit 0 — parser trusts exit code."""
        mock_run.return_value = _make_result(0, "97 passed, 3 skipped\n", "")
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_warnings_not_treated_as_failure(self, mock_run):
        """Warnings with exit 0 — should pass."""
        mock_run.return_value = _make_result(0, "100 passed, 5 warnings\n", "")
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
        """JSON that isn't a Claude result object is an error."""
        parsed = parse_session_result('{"some": "random", "json": true}')
        assert parsed.is_error is False
        assert parsed.subtype == "unknown"

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
    @patch("autoskillit.server.run_managed_async")
    async def test_retry_reason_is_enum_value(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_retry_reason_none_is_enum_value(self, mock_run):
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
        mock_run.return_value = _make_result(0, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}


class TestRunSkillRetrySessionOutcome:
    """run_skill_retry correctly classifies all Claude Code session outcomes."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_detects_max_turns_via_subtype(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_detects_context_limit(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_success_not_retriable(self, mock_run):
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
        mock_run.return_value = _make_result(0, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_execution_error_not_retriable(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_unparseable_stdout_not_retriable(self, mock_run):
        """Non-JSON stdout -> needs_retry=False."""
        mock_run.return_value = _make_result(1, "crash dump", "segfault")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False


class TestRunSkillRetryAgentResult:
    """run_skill_retry result field contains actionable text."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_context_limit_result_is_actionable(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert "prompt is too long" not in result["result"].lower()
        assert result["needs_retry"] is True

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_normal_success_result_passes_through(self, mock_run):
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
        mock_run.return_value = _make_result(0, stdout, "")
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["result"] == "Done."


class TestRunSkillRetryFields:
    """run_skill includes needs_retry and retry_reason for parity."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_includes_needs_retry_false(self, mock_run):
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
        mock_run.return_value = _make_result(0, stdout, "")
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_includes_needs_retry_true_on_context_limit(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert "prompt is too long" not in result["result"].lower()


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_returns_subtype_on_incomplete_session(self, mock_run):
        """run_skill includes subtype when session didn't finish."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
            }
        )
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["session_id"] == "s1"
        assert result["subtype"] == "error_max_turns"

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_returns_is_error_on_context_limit(self, mock_run):
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
        mock_run.return_value = _make_result(1, stdout, "")
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["is_error"] is True
        assert result["subtype"] == "success"

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_handles_empty_stdout(self, mock_run):
        """run_skill returns error result when stdout is empty."""
        mock_run.return_value = _make_result(1, "", "segfault")
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False


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
    @patch("autoskillit.server._run_subprocess")
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
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

    @pytest.mark.asyncio
    @patch("autoskillit.server._run_subprocess")
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
    def _disable_tools(self, monkeypatch):
        """Override the global autouse fixture — start disabled for gate tests."""
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

    @pytest.mark.asyncio
    async def test_tools_return_error_when_disabled(self):
        """All tools return standard gate error when _tools_enabled is False."""
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
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
        assert result["success"] is False
        assert result["is_error"] is True

    def test_tools_disabled_by_default(self):
        """_tools_enabled defaults to False at module load."""
        from autoskillit import server

        assert server._tools_enabled is False

    def test_prompts_registered(self):
        """enable_tools and disable_tools prompts are registered on the server."""
        from fastmcp.prompts import Prompt

        from autoskillit.server import mcp

        prompts = [c for c in mcp._local_provider._components.values() if isinstance(c, Prompt)]
        prompt_names = {p.name for p in prompts}
        assert prompt_names == {"enable_tools", "disable_tools"}

    def test_all_tools_still_registered(self):
        """All 14 tools remain registered (gated + ungated)."""
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
            "autoskillit_status",
            "reset_workspace",
            "read_db",
            "list_skill_scripts",
            "load_skill_script",
            "validate_script",
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
        assert "enable_tools" in parsed["result"]

    def test_all_tools_tagged_automation(self):
        """All 8 tools have the 'automation' tag for future visibility control."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = [c for c in mcp._local_provider._components.values() if isinstance(c, Tool)]
        for tool in tools:
            assert "automation" in tool.tags, f"{tool.name} missing 'automation' tag"


class TestEnableToolsVersionReporting:
    """enable_tools returns version info and warns on mismatch."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self, monkeypatch):
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

    @staticmethod
    def _prompt_text(result) -> str:
        """Extract the text content from a PromptResult."""
        content = result.messages[0].content
        return content.text if hasattr(content, "text") else str(content)

    def test_enable_tools_instructs_status_call(self):
        from autoskillit.server import enable_tools

        result = enable_tools()
        msg = self._prompt_text(result)
        assert "autoskillit_status" in msg

    def test_enable_tools_still_enables_on_mismatch(self, tmp_path, monkeypatch):
        from autoskillit import server
        from autoskillit.server import enable_tools

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path))
        enable_tools()
        assert server._tools_enabled is True


class TestConfigDrivenBehavior:
    """S1-S10: Verify tools use config instead of hardcoded values."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_test_check_uses_config_command(self, mock_run, monkeypatch):
        """S1: test_check runs _config.test_check.command."""
        from autoskillit import server
        from autoskillit.config import TestCheckConfig

        cfg = AutomationConfig(test_check=TestCheckConfig(command=["pytest", "-x"], timeout=300))
        monkeypatch.setattr(server, "_config", cfg)

        mock_run.return_value = _make_result(0, "100 passed\n", "")
        await test_check(worktree_path="/tmp/wt")

        call_args = mock_run.call_args
        assert call_args[0][0] == ["pytest", "-x"]
        assert call_args[1]["timeout"] == 300

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_classify_fix_uses_config_prefixes(self, mock_run, monkeypatch):
        """S2: classify_fix uses _config.classify_fix.path_prefixes."""
        from autoskillit import server

        cfg = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=["src/custom/"]))
        monkeypatch.setattr(server, "_config", cfg)

        changed = "src/custom/handler.py\nsrc/other/util.py\n"
        mock_run.return_value = _make_result(0, changed, "")
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert "src/custom/handler.py" in result["critical_files"]

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_classify_fix_empty_prefixes_always_partial(self, mock_run, monkeypatch):
        """S3: Empty prefix list -> always returns partial_restart."""
        from autoskillit import server

        cfg = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=[]))
        monkeypatch.setattr(server, "_config", cfg)

        changed = "src/core/handler.py\n"
        mock_run.return_value = _make_result(0, changed, "")
        result = json.loads(await classify_fix(worktree_path="/tmp/wt", base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_reset_workspace_uses_config_command(self, mock_run, monkeypatch, tmp_path):
        """S4: reset_workspace runs _config.reset_workspace.command."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["make", "reset"]))
        monkeypatch.setattr(server, "_config", cfg)

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        mock_run.return_value = _make_result(0, "", "")

        await reset_workspace(test_dir=str(workspace))
        assert mock_run.call_args[0][0] == ["make", "reset"]

    @pytest.mark.asyncio
    async def test_reset_workspace_not_configured_returns_error(self, monkeypatch, tmp_path):
        """S5: command=None -> returns not-configured error."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))
        monkeypatch.setattr(server, "_config", cfg)

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_reset_workspace_uses_config_preserve_dirs(
        self, mock_run, monkeypatch, tmp_path
    ):
        """S6: Preserves _config.reset_workspace.preserve_dirs."""
        from autoskillit import server

        cfg = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["true"],
                preserve_dirs={"keep_me"},
            )
        )
        monkeypatch.setattr(server, "_config", cfg)

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "keep_me").mkdir()
        (workspace / "delete_me").touch()
        mock_run.return_value = _make_result(0, "", "")

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "keep_me" in result["skipped"]
        assert "delete_me" in result["deleted"]
        assert (workspace / "keep_me").exists()
        assert not (workspace / "delete_me").exists()

    def test_dry_walkthrough_uses_config_marker(self, monkeypatch, tmp_path):
        """S7: Gate checks _config.implement_gate.marker."""
        from autoskillit import server
        from autoskillit.config import ImplementGateConfig

        cfg = AutomationConfig(implement_gate=ImplementGateConfig(marker="CUSTOM MARKER"))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("CUSTOM MARKER\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # passes with custom marker

        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None  # fails — marker doesn't match

    def test_dry_walkthrough_uses_config_skill_names(self, monkeypatch, tmp_path):
        """S8: Gate checks _config.implement_gate.skill_names."""
        from autoskillit import server
        from autoskillit.config import ImplementGateConfig

        cfg = AutomationConfig(implement_gate=ImplementGateConfig(skill_names={"/custom-impl"}))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker")

        result = _check_dry_walkthrough(f"/custom-impl {plan}", str(tmp_path))
        assert result is not None  # /custom-impl is gated

        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # /autoskillit:implement-worktree is NOT gated (not in skill_names)

    @pytest.mark.asyncio
    @patch("autoskillit.server._run_subprocess")
    async def test_merge_worktree_uses_config_test_command(self, mock_run, monkeypatch, tmp_path):
        """S9: Merge's test gate runs _config.test_check.command."""
        from autoskillit import server
        from autoskillit.config import TestCheckConfig

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
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

        # Verify the test command was ["make", "test"]
        test_call = mock_run.call_args_list[2]
        assert test_call[0][0] == ["make", "test"]

    @pytest.mark.asyncio
    async def test_require_enabled_still_gates_execution(self, monkeypatch):
        """S10: _require_enabled() defense-in-depth still works with config."""
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)
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

        with patch("autoskillit.server.shutil.rmtree", side_effect=selective_rmtree):
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
    async def test_reset_test_dir_returns_partial_failure_json(self, tmp_path):
        """1e: reset_test_dir returns structured JSON on partial failure."""
        from autoskillit import server

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "ok_file").touch()

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[],
        )
        with patch.object(server, "_delete_directory_contents", return_value=mock_result):
            result = json.loads(await reset_test_dir(test_dir=str(workspace), force=False))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
        assert "ok_file" in result["deleted"]

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_reset_workspace_returns_partial_failure_json(
        self, mock_run, monkeypatch, tmp_path
    ):
        """1f: reset_workspace returns structured JSON on partial failure."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        monkeypatch.setattr(server, "_config", cfg)

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        mock_run.return_value = _make_result(0, "", "")

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[".cache"],
        )
        with patch.object(server, "_delete_directory_contents", return_value=mock_result):
            result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]


# ---------------------------------------------------------------------------
# Step 2: Safety config wiring
# ---------------------------------------------------------------------------


class TestSafetyConfigWiring:
    """Safety config fields are read at the point of enforcement."""

    @pytest.mark.asyncio
    async def test_reset_test_dir_allows_with_marker(self, tmp_path):
        """2a: Directory with marker passes the reset guard."""
        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")
        (target / "file.txt").touch()

        result = json.loads(await reset_test_dir(test_dir=str(target), force=False))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_reset_test_dir_enforces_marker_when_missing(self, tmp_path):
        """2b: Missing marker blocks reset_test_dir."""
        target = tmp_path / "unmarked"
        target.mkdir()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reset_workspace_enforces_marker(self, monkeypatch, tmp_path):
        """2c: reset_workspace requires marker, then checks command config."""
        from autoskillit import server

        cfg = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))
        monkeypatch.setattr(server, "_config", cfg)

        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")

        result = json.loads(await reset_workspace(test_dir=str(target)))
        # Should pass marker guard but fail on "not configured"
        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.asyncio
    @patch("autoskillit.server._run_subprocess")
    async def test_merge_worktree_skips_test_gate_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2d: test_gate_on_merge=False skips test execution."""
        from autoskillit import server

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
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_retry_skips_dry_walkthrough_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2e: require_dry_walkthrough=False bypasses dry-walkthrough gate."""
        from autoskillit import server

        cfg = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        mock_run.return_value = _make_result(0, '{"result": "done"}', "")
        result = json.loads(
            await run_skill_retry(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["subtype"] != "gate_error"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_skill_enforces_dry_walkthrough_when_enabled(self, tmp_path):
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
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_skips_dry_walkthrough_when_disabled(
        self, mock_run, monkeypatch, tmp_path
    ):
        """2g: run_skill skips dry-walkthrough gate when disabled."""
        from autoskillit import server

        cfg = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))
        monkeypatch.setattr(server, "_config", cfg)

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        mock_run.return_value = _make_result(0, '{"result": "done"}', "")
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
    @patch("autoskillit.server._run_subprocess")
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
    @patch("autoskillit.server._run_subprocess")
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
    @patch("autoskillit.server._run_subprocess")
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
        assert result["failed_step"] == MergeFailedStep.FETCH


# ---------------------------------------------------------------------------
# run_python tool
# ---------------------------------------------------------------------------


class TestRunPython:
    """run_python tool: import, call, timeout, async support."""

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
        from unittest.mock import MagicMock, patch

        async def _hang(**_kw: object) -> None:
            await _aio.sleep(300)

        mock_module = MagicMock()
        mock_module.hang_fn = _hang

        with patch("autoskillit.server.importlib.import_module", return_value=mock_module):
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
        assert "forbidden" in result["error"].lower() or "SELECT" in result["error"]

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
    async def test_gated_when_disabled(self, sample_db, monkeypatch):
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)
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
    async def test_max_rows_truncation(self, sample_db, monkeypatch):
        from autoskillit import server
        from autoskillit.config import AutomationConfig

        cfg = AutomationConfig(read_db=ReadDbConfig(max_rows=2))
        monkeypatch.setattr(server, "_config", cfg)
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
    async def test_query_timeout(self, sample_db, monkeypatch):
        from autoskillit import server
        from autoskillit.config import AutomationConfig

        cfg = AutomationConfig(read_db=ReadDbConfig(timeout=1))
        monkeypatch.setattr(server, "_config", cfg)
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
    def _disable_tools(self, monkeypatch):
        from autoskillit import server

        monkeypatch.setattr(server, "_tools_enabled", False)

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
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_prefixes_skill_command(self, mock_run):
        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate error", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert cmd[2].startswith("Use /investigate error")

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_no_prefix_for_plain_prompt(self, mock_run):
        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("Fix the bug in main.py", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert cmd[2].startswith("Fix the bug in main.py")

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_includes_completion_directive(self, mock_run):
        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate error", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "%%AUTOSKILLIT_COMPLETE%%" in cmd[2]


class TestRunSkillRetryPrefix:
    """run_skill_retry passes prefixed command to subprocess."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_retry_prefixes_skill_command(self, mock_run):
        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill_retry("/investigate error", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert cmd[2].startswith("Use /investigate error")

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_retry_no_prefix_for_plain_prompt(self, mock_run):
        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill_retry("Fix the bug in main.py", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert cmd[2].startswith("Fix the bug in main.py")


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.asyncio
    async def test_gate_still_fires_for_implement_skill(self, tmp_path):
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
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_timeout_from_config(self, mock_run, monkeypatch):
        """run_skill uses _config.run_skill.timeout instead of hardcoded value."""
        from autoskillit import server

        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(timeout=120)
        cfg.safety.require_dry_walkthrough = False
        monkeypatch.setattr(server, "_config", cfg)

        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate foo", "/tmp")

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 120


class TestRunSkillInjectsCompletionDirective:
    """run_skill injects completion directive into the skill command."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_injects_completion_directive(self, mock_run, monkeypatch):
        """Skill command passed to claude -p contains the completion marker instruction."""
        from autoskillit import server

        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        monkeypatch.setattr(server, "_config", cfg)

        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate foo", "/tmp")

        cmd = mock_run.call_args[0][0]
        # The -p argument is at index 2
        skill_arg = cmd[2]
        assert "%%AUTOSKILLIT_COMPLETE%%" in skill_arg
        assert "ORCHESTRATION DIRECTIVE" in skill_arg


class TestRunSkillPassesSessionLogDir:
    """run_skill passes session_log_dir derived from cwd."""

    @pytest.mark.asyncio
    @patch("autoskillit.server.run_managed_async")
    async def test_run_skill_passes_session_log_dir(self, mock_run, monkeypatch):
        """run_managed_async receives session_log_dir derived from cwd."""
        from autoskillit import server

        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        monkeypatch.setattr(server, "_config", cfg)

        mock_run.return_value = _make_result(
            0,
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}',
            "",
        )
        await run_skill("/investigate foo", "/some/project")

        call_kwargs = mock_run.call_args[1]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir
        assert "-some-project" in str(expected_dir)


class TestStalenessReturnsNeedsRetry:
    """Stale SubprocessResult triggers needs_retry response."""

    def test_staleness_returns_needs_retry(self):
        """A stale result produces needs_retry=True, retry_reason='resume'."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            timed_out=False,
            pid=12345,
            stale=True,
        )
        response = json.loads(_build_skill_result(stale_result))
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
    }

    def test_empty_stdout_exit_zero_is_failure(self):
        """Exit 0 with empty stdout is NOT success — output was lost."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", timed_out=False, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
        assert response["success"] is False
        assert response["is_error"] is True

    def test_timed_out_session_is_failure(self):
        """Timed-out sessions are always failures, regardless of partial stdout."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", timed_out=True, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "timeout"

    def test_stale_session_is_failure(self):
        """Stale sessions are failures (even though retriable)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", timed_out=False, pid=1, stale=True
        )
        response = json.loads(_build_skill_result(result_obj))
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
            returncode=0, stdout=valid_json, stderr="", timed_out=False, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
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
            returncode=1, stdout=valid_json, stderr="", timed_out=False, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
        assert response["success"] is False

    def test_gate_disabled_schema(self):
        """Gate-disabled response has standard keys."""
        import autoskillit.server as srv

        original = srv._tools_enabled
        try:
            srv._tools_enabled = False
            response = json.loads(srv._require_enabled())
            assert set(response.keys()) == self.EXPECTED_SKILL_KEYS
        finally:
            srv._tools_enabled = original

    def test_stale_schema(self):
        """Stale response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", timed_out=False, pid=1, stale=True
        )
        response = json.loads(_build_skill_result(result_obj))
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_timeout_schema(self):
        """Timeout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", timed_out=True, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
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
            returncode=0, stdout=valid_json, stderr="", timed_out=False, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_empty_stdout_schema(self):
        """Empty stdout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", timed_out=False, pid=1, stale=False
        )
        response = json.loads(_build_skill_result(result_obj))
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 8-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self):
        """Gate errors must use the same schema as normal responses."""
        import autoskillit.server as srv

        original = srv._tools_enabled
        try:
            srv._tools_enabled = False
            gate_result = srv._require_enabled()
            assert gate_result is not None
            response = json.loads(gate_result)
            assert response["success"] is False
            assert response["is_error"] is True
            assert response["needs_retry"] is False
            assert "result" in response
        finally:
            srv._tools_enabled = original

    def test_dry_walkthrough_gate_returns_standard_schema(self, tmp_path):
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
        assert _compute_success(session, returncode=0, timed_out=False, stale=False) is True

    def test_empty_result_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert _compute_success(session, returncode=0, timed_out=False, stale=False) is False

    def test_nonzero_exit_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert _compute_success(session, returncode=1, timed_out=False, stale=False) is False

    def test_is_error_true_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=True, result="Error occurred", session_id="s1"
        )
        assert _compute_success(session, returncode=0, timed_out=False, stale=False) is False

    def test_timed_out_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert _compute_success(session, returncode=0, timed_out=True, stale=False) is False

    def test_stale_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert _compute_success(session, returncode=0, timed_out=False, stale=True) is False

    def test_unknown_subtype_is_failure(self):
        session = ClaudeSessionResult(
            subtype="unknown", is_error=False, result="Done.", session_id="s1"
        )
        assert _compute_success(session, returncode=0, timed_out=False, stale=False) is False


class TestLoadSkillScriptFailurePredicates:
    """The load_skill_script tool description documents failure predicates."""

    def test_description_documents_run_skill_failure(self):
        """The routing rules must define failure for run_skill, not just test_check."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = {
            c.name: c
            for c in mcp._local_provider._components.values()
            if isinstance(c, Tool)
        }
        desc = tools["load_skill_script"].description or ""
        assert "run_skill" in desc
        assert "success" in desc.lower()

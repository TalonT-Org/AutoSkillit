"""Tests for autoskillit server workspace tools."""

from __future__ import annotations

import json
import os

import pytest
from autoskillit.server.tools_workspace import reset_test_dir, reset_workspace, test_check

from autoskillit.config import (
    AutomationConfig,
    ResetWorkspaceConfig,
    SafetyConfig,
)
from autoskillit.core.types import AUTOSKILLIT_PRIVATE_ENV_VARS
from autoskillit.workspace import CleanupResult
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

test_check.__test__ = False  # type: ignore[attr-defined]


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

    @pytest.mark.anyio
    async def test_rejects_without_marker(self, tmp_path):
        """reset_workspace rejects directory without marker."""
        workspace = tmp_path / "unmarked"
        workspace.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.anyio
    async def test_rejects_nonexistent_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "does not exist" in result["error"]

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_reset_command_failure(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(1, "", "command not found"))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "error" in result
        assert result["error"] == "reset command failed"
        assert result["exit_code"] == 1

    @pytest.mark.anyio
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


class TestTestCheck:
    """test_check returns unambiguous PASS/FAIL with cross-validation."""

    @pytest.mark.anyio
    async def test_test_check_accessible_without_gate(self, tool_ctx):
        """test_check must not be blocked by gate — _require_enabled() was removed."""
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        # Still need runner to be set up — push a result for it
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        result_str = await test_check(worktree_path="/tmp/wt")
        result = json.loads(result_str)
        assert result.get("subtype") != "gate_error", (
            "test_check must not be gated — _require_enabled() was removed"
        )
        assert "passed" in result

    @pytest.mark.anyio
    async def test_passes_on_clean_run(self, tool_ctx):
        """returncode=0 with passing summary -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_fails_on_nonzero_exit(self, tool_ctx):
        """returncode=1 -> passed=False regardless of output."""
        tool_ctx.runner.push(_make_result(1, "= 3 failed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_cross_validates_exit_code_against_output(self, tool_ctx):
        """returncode=0 but output contains 'failed' -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "= 3 failed, 8538 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_does_not_expose_summary(self, tool_ctx):
        """test_check returns passed + stdout + stderr — no summary, no output_file."""
        tool_ctx.runner.push(
            _make_result(0, "= 100 passed =\nTest output saved to: /tmp/out.txt\n", "")
        )
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert "summary" not in result
        assert "output_file" not in result
        assert "passed" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "duration_seconds" in result

    @pytest.mark.anyio
    async def test_cross_validates_error_in_output(self, tool_ctx):
        """returncode=0 but output contains 'error' -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "= 1 error, 99 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_xfailed_not_treated_as_failure(self, tool_ctx):
        """xfailed tests are expected failures — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 8552 passed, 3 xfailed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_xpassed_not_treated_as_failure(self, tool_ctx):
        """xpassed tests are unexpected passes — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 99 passed, 1 xpassed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_mixed_xfail_with_real_failure(self, tool_ctx):
        """Real failure + xfailed — should still fail on the real failure."""
        tool_ctx.runner.push(_make_result(0, "= 1 failed, 2 xfailed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_skipped_with_exit_zero_passes(self, tool_ctx):
        """Skipped tests with exit 0 — parser trusts exit code."""
        tool_ctx.runner.push(_make_result(0, "= 97 passed, 3 skipped =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_warnings_not_treated_as_failure(self, tool_ctx):
        """Warnings with exit 0 — should pass."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed, 5 warnings =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_bare_q_failures_detected(self, tool_ctx):
        """Bare -q failure line (rc=0 due to PIPESTATUS bug) -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "3 failed, 97 passed in 2.31s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_non_pytest_runner_empty_output_passes(self, tool_ctx):
        """Non-pytest runner: rc=0, empty stdout, empty stderr -> passed=True (trust exit code)."""
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_bare_q_clean_passes(self, tool_ctx):
        """Bare -q all-passing output: rc=0 and summary found -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "100 passed in 1.50s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_test_check_resolves_relative_worktree_path(self, tool_ctx):
        """test_check must apply os.path.realpath() to worktree_path so that relative
        paths are resolved against os.getcwd() consistently, matching reset_test_dir
        and reset_workspace behavior."""
        relative_path = "../some_worktree"
        expected_resolved = os.path.realpath(relative_path)

        await test_check(worktree_path=relative_path)

        _cmd, cwd, _timeout, _kwargs = tool_ctx.runner.call_args_list[-1]
        assert str(cwd) == expected_resolved, (
            f"Expected cwd={expected_resolved!r}, got {str(cwd)!r}. "
            "test_check must apply os.path.realpath() to worktree_path."
        )

    @pytest.mark.anyio
    async def test_test_check_does_not_pass_headless_env_to_subprocess(
        self, tool_ctx, monkeypatch
    ):
        """When AUTOSKILLIT_HEADLESS is set in the calling process (simulating a headless
        MCP server), test_check must not pass it to the subprocess runner."""
        # Simulate running inside a headless session — set all private vars
        for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
            monkeypatch.setenv(var, "1")

        await test_check(worktree_path="/tmp/wt")

        assert tool_ctx.runner.call_args_list, "Runner was not called"
        _cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[-1]
        env = kwargs.get("env")
        assert env is not None, "test_check must pass an explicit env= to the runner"
        for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
            assert var not in env, (
                f"{var} must not appear in env passed to subprocess by test_check"
            )

    @pytest.mark.anyio
    async def test_cargo_nextest_style_passes(self, tool_ctx):
        """Non-pytest runner: rc=0, stderr-only output -> passed=True, stderr surfaced."""
        tool_ctx.runner.push(_make_result(0, "", "PASS [0.5s] 5 tests"))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True
        assert "PASS [0.5s] 5 tests" in result["stderr"]

    @pytest.mark.anyio
    async def test_response_schema_includes_stderr(self, tool_ctx):
        """test_check response contains passed, stdout, and stderr keys."""
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert set(result.keys()) == {"passed", "stdout", "stderr", "duration_seconds"}

    @pytest.mark.anyio
    async def test_pytest_failure_still_detected(self, tool_ctx):
        """Regression guard: pytest failures are still detected after CWA removal."""
        tool_ctx.runner.push(_make_result(0, "= 3 failed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_test_check_response_includes_duration(self, tool_ctx):
        """test_check JSON includes duration_seconds."""
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        raw = await test_check("/tmp/wt")
        data = json.loads(raw)
        assert "duration_seconds" in data
        assert isinstance(data["duration_seconds"], float)
        assert data["duration_seconds"] >= 0.0

    @pytest.mark.anyio
    async def test_test_check_response_includes_filter_stats(self, tool_ctx, monkeypatch):
        """test_check JSON includes filter fields when sidecar is written."""
        from pathlib import Path as _Path

        from autoskillit.core.types import SubprocessResult, TerminationReason

        sidecar_data = {
            "filter_mode": "aggressive",
            "tests_selected": 73,
            "tests_deselected": 275,
        }

        async def fake_runner(command, *, cwd, timeout, env, **kwargs):
            sidecar_path = env.get("AUTOSKILLIT_FILTER_STATS_FILE")
            assert sidecar_path, "AUTOSKILLIT_FILTER_STATS_FILE must be injected into env"
            _Path(sidecar_path).write_text(json.dumps(sidecar_data))
            return SubprocessResult(
                returncode=0,
                stdout="= 73 passed =\n",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )

        monkeypatch.setattr(tool_ctx.tester, "_runner", fake_runner)
        raw = await test_check("/tmp/wt")
        data = json.loads(raw)
        assert data["filter_mode"] == "aggressive"
        assert data["tests_selected"] == 73
        assert data["tests_deselected"] == 275

    @pytest.mark.anyio
    async def test_test_check_response_omits_filter_stats_when_absent(self, tool_ctx):
        """Filter fields are absent (not null) from response when no filter active."""
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        raw = await test_check("/tmp/wt")
        data = json.loads(raw)
        assert "filter_mode" not in data
        assert "tests_selected" not in data


class TestResetGuard:
    """Marker-file-based reset guard for destructive operations."""

    @pytest.mark.anyio
    async def test_reset_test_dir_refuses_without_marker(self, tool_ctx, tmp_path):
        """Directory without marker file is refused."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower() or "reset guard" in result["error"].lower()

    @pytest.mark.anyio
    async def test_reset_test_dir_allows_with_marker(self, tool_ctx, tmp_path):
        """Directory with marker file is cleared."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert not (target / "some_file.txt").exists()

    @pytest.mark.anyio
    async def test_reset_test_dir_preserves_marker(self, tool_ctx, tmp_path):
        """Reset preserves the marker file so the workspace is reusable."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "data.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert (target / ".autoskillit-workspace").is_file()

    @pytest.mark.anyio
    async def test_reset_workspace_refuses_without_marker(self, tool_ctx, tmp_path):
        """reset_workspace also checks for marker."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        target = tmp_path / "workspace"
        target.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert "error" in result

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_custom_marker_name(self, tool_ctx, tmp_path):
        """Config can override marker file name."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(reset_guard_marker=".my-workspace"))
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".my-workspace").touch()
        (target / "file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_force_overrides_marker_check(self, tool_ctx, tmp_path):
        """force=True on reset_test_dir bypasses marker requirement."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "file.txt").touch()
        # No marker, but force=True
        result = json.loads(await reset_test_dir(test_dir=str(target), force=True))
        assert result["success"] is True

    @pytest.mark.anyio
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


@pytest.mark.anyio
async def test_reset_test_dir_returns_partial_failure_json(tool_ctx, tmp_path):
    """reset_test_dir returns structured JSON on partial failure."""
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


@pytest.mark.anyio
async def test_reset_workspace_returns_partial_failure_json(tool_ctx, tmp_path):
    """reset_workspace returns structured JSON on partial failure."""
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


class TestTestCheckTiming:
    """test_check records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_test_check_step_name_records_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        await test_check("/tmp/wt", step_name="test_check")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "test_check" for e in report)

    @pytest.mark.anyio
    async def test_test_check_empty_step_name_skips_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        await test_check("/tmp/wt")
        assert tool_ctx.timing_log.get_report() == []


class TestResetTestDirTiming:
    """reset_test_dir records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_reset_test_dir_step_name_records_timing(self, tool_ctx, tmp_path):
        marker = tmp_path / ".autoskillit-workspace"
        marker.write_text("")
        mock_result = CleanupResult(deleted=[], failed=[], skipped=[])
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        await reset_test_dir(str(tmp_path), step_name="reset")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "reset" for e in report)

    @pytest.mark.anyio
    async def test_reset_test_dir_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        marker = tmp_path / ".autoskillit-workspace"
        marker.write_text("")
        mock_result = CleanupResult(deleted=[], failed=[], skipped=[])
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        await reset_test_dir(str(tmp_path))
        assert tool_ctx.timing_log.get_report() == []

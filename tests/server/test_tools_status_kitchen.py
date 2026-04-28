"""Tests for autoskillit server status tools: kitchen status, pipeline report, and telemetry recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import anyio
import pytest

from autoskillit.config import AutomationConfig, TokenUsageConfig
from autoskillit.core.types import ChannelConfirmation
from autoskillit.execution.github import DefaultGitHubFetcher
from autoskillit.pipeline.audit import FailureRecord
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_execution import run_skill
from autoskillit.server.tools_status import (
    get_pipeline_report,
    get_timing_summary,
    get_token_summary,
    kitchen_status,
)
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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


class TestKitchenStatus:
    """kitchen_status tool returns version health info."""

    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

    @pytest.mark.anyio
    async def test_status_returns_version_info(self, tool_ctx):
        import autoskillit
        from autoskillit.core._type_plugin_source import DirectInstall

        tool_ctx.plugin_source = DirectInstall(plugin_dir=Path(autoskillit.__file__).parent)
        from autoskillit import __version__

        result = json.loads(await kitchen_status())
        assert result["package_version"] == __version__
        assert result["plugin_json_version"] == __version__
        assert result["versions_match"] is True
        assert "warning" not in result

    @pytest.mark.anyio
    async def test_status_reports_mismatch(self, tmp_path, tool_ctx):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        from autoskillit.core._type_plugin_source import DirectInstall

        tool_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)
        result = json.loads(await kitchen_status())
        assert result["versions_match"] is False
        assert "warning" in result
        assert "mismatch" in result["warning"].lower()

    @pytest.mark.anyio
    async def test_status_includes_token_usage_verbosity_default(self):
        """TU_S1: kitchen_status includes token_usage_verbosity key with default 'summary'."""
        result = json.loads(await kitchen_status())
        assert "token_usage_verbosity" in result
        assert result["token_usage_verbosity"] == "summary"

    @pytest.mark.anyio
    async def test_status_reflects_none_verbosity(self, tool_ctx):
        """TU_S2: kitchen_status reflects 'none' verbosity from config."""
        cfg = AutomationConfig()
        cfg.token_usage = TokenUsageConfig(verbosity="none")
        tool_ctx.config = cfg
        result = json.loads(await kitchen_status())
        assert result["token_usage_verbosity"] == "none"

    @pytest.mark.anyio
    async def test_kitchen_status_includes_github_config(self, tool_ctx):
        tool_ctx.config.github.default_repo = "owner/repo"
        status = json.loads(await kitchen_status())
        assert "github_default_repo" in status
        assert status["github_default_repo"] == "owner/repo"
        assert "github_token_configured" in status

    @pytest.mark.anyio
    async def test_kitchen_status_github_token_configured_true_from_client(self, tool_ctx):
        """kitchen_status must read github_token_configured from ctx.github_client.has_token."""
        tool_ctx.github_client = DefaultGitHubFetcher(token="my-token")
        status = json.loads(await kitchen_status())
        assert status["github_token_configured"] is True

    @pytest.mark.anyio
    async def test_kitchen_status_github_token_not_configured_from_client(
        self, tool_ctx, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        tool_ctx.github_client = DefaultGitHubFetcher(token=None)
        status = json.loads(await kitchen_status())
        assert status["github_token_configured"] is False

    @pytest.mark.anyio
    async def test_kitchen_status_github_token_does_not_reflect_post_construction_env(
        self, tool_ctx, monkeypatch
    ):
        """kitchen_status must NOT re-read os.environ — reflects ctx.github_client.has_token."""
        tool_ctx.github_client = DefaultGitHubFetcher(token=None)
        monkeypatch.setenv("GITHUB_TOKEN", "set-after-construction")
        status = json.loads(await kitchen_status())
        assert status["github_token_configured"] is False

    @pytest.mark.anyio
    async def test_kitchen_status_has_no_gate_file_exists_field(self, tool_ctx):
        """kitchen_status must not report gate_file_exists."""
        result = json.loads(await kitchen_status())
        assert "gate_file_exists" not in result

    @pytest.mark.anyio
    async def test_kitchen_status_has_no_split_brain_warning(self, tool_ctx, tmp_path):
        """A file at the old gate path must not trigger any warning in kitchen_status."""
        gate_dir = tmp_path / ".autoskillit" / "temp"
        gate_dir.mkdir(parents=True)
        (gate_dir / ".kitchen_gate").write_text("{}")
        result_str = json.dumps(json.loads(await kitchen_status()))
        assert "stale" not in result_str.lower()
        assert "gate_file" not in result_str.lower()


class TestGetPipelineReport:
    """get_pipeline_report is a gated tool that returns accumulated failures."""

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        """get_pipeline_report returns gate_error when kitchen gate is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await get_pipeline_report())
        assert result.get("success") is False
        assert result.get("subtype") == "gate_error"

    @pytest.mark.anyio
    async def test_returns_empty_initially(self, tool_ctx):
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 0
        assert result["failures"] == []

    @pytest.mark.anyio
    async def test_accumulates_failures_from_run_skill(self, tool_ctx):

        tool_ctx.gate = DefaultGateState(enabled=True)
        tool_ctx.runner.push(
            _make_result(
                returncode=1,
                stdout=_failed_session_json(),
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
        )
        await run_skill(skill_command="/autoskillit:test", cwd="/tmp")
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 1
        assert result["failures"][0]["skill_command"].startswith("/autoskillit:test")

    @pytest.mark.anyio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.audit.record_failure(_make_failure_record())
        result = json.loads(await get_pipeline_report(clear=True))
        assert result["total_failures"] == 1
        result2 = json.loads(await get_pipeline_report())
        assert result2["total_failures"] == 0

    @pytest.mark.anyio
    async def test_get_pipeline_report_awaits_startup_ready(self, tool_ctx, monkeypatch):
        """get_pipeline_report must await _startup_ready before accessing audit data."""
        import autoskillit.server._state as _state_mod

        ready = asyncio.Event()
        monkeypatch.setattr(_state_mod, "_startup_ready", ready)

        task = asyncio.create_task(get_pipeline_report())
        await asyncio.sleep(0.05)
        assert not task.done(), "get_pipeline_report returned before _startup_ready was set"

        ready.set()
        await asyncio.sleep(0.05)
        assert task.done(), "get_pipeline_report did not unblock after _startup_ready was set"


def test_check_quota_absent_from_mcp_registry(tool_ctx):
    """check_quota must not appear in the agent-visible tool list.
    Quota enforcement is the PreToolUse hook's responsibility, not an MCP tool."""
    from autoskillit.server import mcp

    tools = anyio.run(mcp.list_tools)
    assert "check_quota" not in {t.name for t in tools}, (
        "check_quota must be removed from the MCP registry. "
        "Agents should not call it — the PreToolUse hook enforces quota automatically."
    )


class TestTelemetryRecoveryData:
    """MCP status tools return data populated via load_from_log_dir recovery."""

    def _write_token_session(
        self, log_root: Path, dir_name: str, step_name: str, input_tokens: int
    ) -> None:
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        tu = {
            "step_name": step_name,
            "input_tokens": input_tokens,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "timing_seconds": 10.0,
        }
        (session_dir / "token_usage.json").write_text(json.dumps(tu))
        idx = {
            "dir_name": dir_name,
            "timestamp": "2026-03-07T00:00:00+00:00",
            "session_id": dir_name,
        }
        with (log_root / "sessions.jsonl").open("a") as f:
            f.write(json.dumps(idx) + "\n")

    def _write_timing_session(
        self, log_root: Path, dir_name: str, step_name: str, total_seconds: float
    ) -> None:
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        st = {"step_name": step_name, "total_seconds": total_seconds}
        (session_dir / "step_timing.json").write_text(json.dumps(st))
        idx = {
            "dir_name": dir_name,
            "timestamp": "2026-03-07T00:00:00+00:00",
            "session_id": dir_name,
        }
        with (log_root / "sessions.jsonl").open("a") as f:
            f.write(json.dumps(idx) + "\n")

    def _write_audit_session(self, log_root: Path, dir_name: str) -> None:
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": "2026-03-07T00:00:00Z",
            "skill_command": "/autoskillit:implement-worktree",
            "exit_code": 1,
            "subtype": "error",
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "oops",
        }
        (session_dir / "audit_log.json").write_text(json.dumps([record]))
        idx = {
            "dir_name": dir_name,
            "timestamp": "2026-03-07T00:00:00+00:00",
            "session_id": dir_name,
        }
        with (log_root / "sessions.jsonl").open("a") as f:
            f.write(json.dumps(idx) + "\n")

    @pytest.mark.anyio
    async def test_token_summary_reflects_recovered_data(self, tool_ctx, tmp_path):
        """get_token_summary returns data loaded via load_from_log_dir."""
        log_root = tmp_path / "logs"
        self._write_token_session(log_root, "s001", "implement", 500)
        tool_ctx.token_log.load_from_log_dir(log_root)
        result = json.loads(await get_token_summary())
        steps = {s["step_name"]: s for s in result["steps"]}
        assert "implement" in steps
        assert steps["implement"]["input_tokens"] == 500

    @pytest.mark.anyio
    async def test_timing_summary_reflects_recovered_data(self, tool_ctx, tmp_path):
        """get_timing_summary returns data loaded via load_from_log_dir."""
        log_root = tmp_path / "logs"
        self._write_timing_session(log_root, "s001", "plan", 99.0)
        tool_ctx.timing_log.load_from_log_dir(log_root)
        result = json.loads(await get_timing_summary())
        steps = {s["step_name"]: s for s in result["steps"]}
        assert "plan" in steps
        assert steps["plan"]["total_seconds"] == pytest.approx(99.0)

    @pytest.mark.anyio
    async def test_pipeline_report_reflects_recovered_audit(self, tool_ctx, tmp_path):
        """get_pipeline_report returns failures loaded via load_from_log_dir."""
        log_root = tmp_path / "logs"
        self._write_audit_session(log_root, "s001")
        tool_ctx.audit.load_from_log_dir(log_root)
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 1
        assert result["failures"][0]["skill_command"] == "/autoskillit:implement-worktree"

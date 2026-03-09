"""Tests for autoskillit server status tools."""

from __future__ import annotations

import json
import os
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
    """kitchen_status tool returns version health info (ungated)."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx, monkeypatch, tmp_path):
        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.chdir(tmp_path)

    @pytest.mark.anyio
    async def test_status_returns_version_info(self, tool_ctx):
        import autoskillit

        tool_ctx.plugin_dir = str(Path(autoskillit.__file__).parent)
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
        tool_ctx.plugin_dir = str(tmp_path)
        result = json.loads(await kitchen_status())
        assert result["versions_match"] is False
        assert "warning" in result
        assert "mismatch" in result["warning"].lower()

    @pytest.mark.anyio
    async def test_status_works_without_enable(self, tool_ctx):
        assert tool_ctx.gate.enabled is False
        result = json.loads(await kitchen_status())
        assert result["tools_enabled"] is False
        assert isinstance(result["package_version"], str) and result["package_version"]

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
    async def test_kitchen_status_reports_gate_file_exists(self, monkeypatch, tmp_path, tool_ctx):
        """kitchen_status must include gate_file_exists field."""
        monkeypatch.chdir(tmp_path)
        gate_dir = tmp_path / ".autoskillit" / "temp"
        gate_dir.mkdir(parents=True)
        (gate_dir / ".kitchen_gate").write_text(
            json.dumps({"pid": os.getpid(), "opened_at": "2026-01-01T00:00:00Z"})
        )
        result = json.loads(await kitchen_status())
        assert result["gate_file_exists"] is True

    @pytest.mark.anyio
    async def test_kitchen_status_reports_no_gate_file(self, monkeypatch, tmp_path, tool_ctx):
        """kitchen_status must report gate_file_exists=False when no gate file."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await kitchen_status())
        assert result["gate_file_exists"] is False

    @pytest.mark.anyio
    async def test_kitchen_status_warns_on_split_brain(self, monkeypatch, tmp_path, tool_ctx):
        """When gate_file_exists=True but tools_enabled=False, emit warning."""
        monkeypatch.chdir(tmp_path)
        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_dir = tmp_path / ".autoskillit" / "temp"
        gate_dir.mkdir(parents=True)
        (gate_dir / ".kitchen_gate").write_text(
            json.dumps({"pid": 999999999, "opened_at": "2026-01-01T00:00:00Z"})
        )
        result = json.loads(await kitchen_status())
        assert result["tools_enabled"] is False
        assert result["gate_file_exists"] is True
        assert "stale" in result.get("warning", "").lower()

    @pytest.mark.anyio
    async def test_kitchen_status_no_warning_when_consistent(
        self, monkeypatch, tmp_path, tool_ctx
    ):
        """No warning when gate_file_exists and tools_enabled are consistent."""
        import autoskillit

        monkeypatch.chdir(tmp_path)
        tool_ctx.gate = DefaultGateState(enabled=True)
        tool_ctx.plugin_dir = str(Path(autoskillit.__file__).parent)
        gate_dir = tmp_path / ".autoskillit" / "temp"
        gate_dir.mkdir(parents=True)
        (gate_dir / ".kitchen_gate").write_text(
            json.dumps({"pid": os.getpid(), "opened_at": "2026-01-01T00:00:00Z"})
        )
        result = json.loads(await kitchen_status())
        assert result["tools_enabled"] is True
        assert result["gate_file_exists"] is True
        assert "warning" not in result

    @pytest.mark.anyio
    async def test_kitchen_status_stale_warning_references_correct_path(
        self, monkeypatch, tmp_path, tool_ctx
    ):
        """Split-brain warning must reference .autoskillit/temp/ not temp/."""
        monkeypatch.chdir(tmp_path)
        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_dir = tmp_path / ".autoskillit" / "temp"
        gate_dir.mkdir(parents=True)
        (gate_dir / ".kitchen_gate").write_text(
            json.dumps({"pid": 999999999, "opened_at": "2026-01-01T00:00:00Z"})
        )
        result = json.loads(await kitchen_status())
        warning = result.get("warning", "")
        assert ".autoskillit/temp/.kitchen_gate" in warning, (
            f"Warning must reference .autoskillit/temp/.kitchen_gate, got: {warning!r}"
        )


class TestGetPipelineReport:
    """get_pipeline_report is ungated and returns accumulated failures."""

    # Override conftest to test WITHOUT open_kitchen
    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_ungated_returns_empty_initially(self, tool_ctx):
        result = json.loads(await get_pipeline_report())
        assert result["total_failures"] == 0
        assert result["failures"] == []

    @pytest.mark.anyio
    async def test_ungated_does_not_require_open_kitchen(self, tool_ctx):
        """Must succeed even when gate is disabled."""
        result = json.loads(await get_pipeline_report())
        assert "error" not in result

    @pytest.mark.anyio
    async def test_accumulates_failures_from_run_skill(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

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


class TestGetTokenSummary:
    """get_token_summary is ungated and returns accumulated token usage."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_ungated_does_not_require_open_kitchen(self, tool_ctx):
        result = json.loads(await get_token_summary())
        assert "error" not in result

    @pytest.mark.anyio
    async def test_returns_empty_steps_initially(self, tool_ctx):
        result = json.loads(await get_token_summary())
        assert result["steps"] == []
        assert result["total"]["input_tokens"] == 0
        assert result["total"]["output_tokens"] == 0
        assert result["total"]["cache_creation_input_tokens"] == 0
        assert result["total"]["cache_read_input_tokens"] == 0

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_step_includes_elapsed_seconds(self, tool_ctx):
        """Each step dict in the response includes elapsed_seconds."""
        start = "2026-01-01T00:00:00+00:00"
        end = "2026-01-01T00:00:30+00:00"
        tool_ctx.token_log.record("deploy", {"input_tokens": 200}, start_ts=start, end_ts=end)
        result = json.loads(await get_token_summary())
        assert result["steps"][0]["elapsed_seconds"] == pytest.approx(30.0)

    @pytest.mark.anyio
    async def test_total_includes_total_elapsed_seconds(self, tool_ctx):
        """Total dict includes total_elapsed_seconds summed across all steps."""
        tool_ctx.token_log.record(
            "a",
            {"input_tokens": 10},
            start_ts="2026-01-01T00:00:00+00:00",
            end_ts="2026-01-01T00:00:05+00:00",
        )
        tool_ctx.token_log.record(
            "b",
            {"input_tokens": 20},
            start_ts="2026-01-01T00:01:00+00:00",
            end_ts="2026-01-01T00:01:08+00:00",
        )
        result = json.loads(await get_token_summary())
        assert "total_elapsed_seconds" in result["total"]
        assert result["total"]["total_elapsed_seconds"] == pytest.approx(13.0)


class TestGetTimingSummary:
    """get_timing_summary is ungated and returns accumulated wall-clock timing."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_ungated_does_not_require_open_kitchen(self, tool_ctx):
        result = json.loads(await get_timing_summary())
        assert "error" not in result

    @pytest.mark.anyio
    async def test_returns_empty_steps_initially(self, tool_ctx):
        result = json.loads(await get_timing_summary())
        assert result["steps"] == []
        assert result["total"]["total_seconds"] == 0.0

    @pytest.mark.anyio
    async def test_returns_entry_per_step_name(self, tool_ctx):
        tool_ctx.timing_log.record("clone", 4.0)
        tool_ctx.timing_log.record("test_check", 12.0)
        result = json.loads(await get_timing_summary())
        assert len(result["steps"]) == 2
        step_names = {s["step_name"] for s in result["steps"]}
        assert step_names == {"clone", "test_check"}

    @pytest.mark.anyio
    async def test_multiple_invocations_same_step_are_summed(self, tool_ctx):
        tool_ctx.timing_log.record("impl", 10.0)
        tool_ctx.timing_log.record("impl", 10.0)
        tool_ctx.timing_log.record("impl", 10.0)
        result = json.loads(await get_timing_summary())
        assert len(result["steps"]) == 1
        assert result["steps"][0]["total_seconds"] == 30.0
        assert result["steps"][0]["invocation_count"] == 3

    @pytest.mark.anyio
    async def test_total_field_sums_all_steps(self, tool_ctx):
        tool_ctx.timing_log.record("a", 5.0)
        tool_ctx.timing_log.record("b", 8.0)
        result = json.loads(await get_timing_summary())
        assert result["total"]["total_seconds"] == 13.0

    @pytest.mark.anyio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.timing_log.record("clone", 4.0)
        result = json.loads(await get_timing_summary(clear=True))
        assert len(result["steps"]) == 1
        result2 = json.loads(await get_timing_summary())
        assert result2["steps"] == []

    @pytest.mark.anyio
    async def test_response_shape(self, tool_ctx):
        tool_ctx.timing_log.record("plan", 3.0)
        result = json.loads(await get_timing_summary())
        assert "steps" in result
        assert "total" in result
        assert isinstance(result["steps"], list)
        assert "total_seconds" in result["total"]


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

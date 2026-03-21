"""Tests for autoskillit server status tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    get_quota_events,
    get_timing_summary,
    get_token_summary,
    kitchen_status,
    write_telemetry_files,
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
    """kitchen_status tool returns version health info."""

    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx, monkeypatch, tmp_path):
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


class TestGetTokenSummary:
    """get_token_summary is a gated tool that returns accumulated token usage."""

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        """get_token_summary returns gate_error when kitchen gate is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await get_token_summary())
        assert result.get("success") is False
        assert result.get("subtype") == "gate_error"

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
    """get_timing_summary is a gated tool that returns accumulated wall-clock timing."""

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        """get_timing_summary returns gate_error when kitchen gate is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await get_timing_summary())
        assert result.get("success") is False
        assert result.get("subtype") == "gate_error"

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


class TestGetQuotaEvents:
    @pytest.mark.anyio
    async def test_returns_events_from_jsonl(self, tool_ctx, tmp_path, monkeypatch):
        events = [
            {
                "ts": "2026-03-10T10:00:00+00:00",
                "event": "approved",
                "threshold": 90.0,
                "utilization": 50.0,
            },
            {
                "ts": "2026-03-10T11:00:00+00:00",
                "event": "blocked",
                "threshold": 90.0,
                "utilization": 92.5,
            },
        ]
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "quota_events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n"
        )
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events())
        assert result["total_count"] == 2
        assert result["events"][0]["event"] == "blocked"  # most recent first

    @pytest.mark.anyio
    async def test_limits_to_n_events(self, tool_ctx, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        lines = [
            json.dumps({"ts": f"2026-03-10T{h:02d}:00:00+00:00", "event": "approved"})
            for h in range(10)
        ]
        (log_dir / "quota_events.jsonl").write_text("\n".join(lines) + "\n")
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events(n=3))
        assert len(result["events"]) == 3
        assert result["total_count"] == 10
        # most-recent-first: hours 09, 08, 07
        assert result["events"][0]["ts"] == "2026-03-10T09:00:00+00:00"
        assert result["events"][1]["ts"] == "2026-03-10T08:00:00+00:00"
        assert result["events"][2]["ts"] == "2026-03-10T07:00:00+00:00"

    @pytest.mark.anyio
    async def test_returns_empty_when_file_missing(self, tool_ctx, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events())
        assert result["events"] == []
        assert result["total_count"] == 0


class TestWriteTelemetryFiles:
    @pytest.mark.anyio
    async def test_writes_token_summary_markdown(self, tool_ctx, tmp_path):
        tool_ctx.token_log.record(
            "step1",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        path = Path(result["token_summary_path"])
        assert path.exists()
        content = path.read_text()
        assert "step1" in content
        # Format-structural assertions (table, not bullet list)
        assert "| Step |" in content
        assert "|---" in content
        assert "- input_tokens:" not in content
        assert "# Token Summary" not in content

    @pytest.mark.anyio
    async def test_writes_timing_summary_markdown(self, tool_ctx, tmp_path):
        tool_ctx.timing_log.record("step1", 12.5)
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        path = Path(result["timing_summary_path"])
        assert path.exists()
        content = path.read_text()
        assert "step1" in content
        # Format-structural assertions (table, not bullet list)
        assert "| Step |" in content
        assert "|---" in content
        assert "- total_seconds:" not in content
        assert "# Timing Summary" not in content

    @pytest.mark.anyio
    async def test_token_file_uses_wall_clock_seconds(self, tool_ctx, tmp_path):
        """write_telemetry_files merges wall_clock_seconds from timing log."""
        tool_ctx.token_log.record(
            "deploy",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=5.0,
        )
        tool_ctx.timing_log.record("deploy", 120.0)
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        content = Path(result["token_summary_path"]).read_text()
        # Should show 2m 0s (wall_clock=120), not 5s (elapsed)
        assert "2m 0s" in content

    @pytest.mark.anyio
    async def test_creates_output_dir_if_missing(self, tool_ctx, tmp_path):
        out = str(tmp_path / "nested" / "telemetry")
        result = json.loads(await write_telemetry_files(out))
        assert Path(result["token_summary_path"]).exists()
        assert Path(result["timing_summary_path"]).exists()

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await write_telemetry_files("/tmp"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"


class TestGetTokenSummaryFormat:
    """Tests for get_token_summary format parameter."""

    @pytest.mark.anyio
    async def test_format_json_default(self, tool_ctx):
        """format='json' (default) returns JSON dict."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = json.loads(await get_token_summary())
        assert "steps" in result
        assert "total" in result

    @pytest.mark.anyio
    async def test_format_table_returns_markdown(self, tool_ctx):
        """format='table' returns a markdown table string."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = await get_token_summary(format="table")
        assert "| Step |" in result
        assert "|---" in result
        assert "plan" in result
        assert "**Total**" in result
        # Not JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    @pytest.mark.anyio
    async def test_format_table_with_clear(self, tool_ctx):
        """format='table' + clear=True clears the log after returning."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = await get_token_summary(clear=True, format="table")
        assert "plan" in result
        result2 = json.loads(await get_token_summary())
        assert result2["steps"] == []


class TestGetTimingSummaryFormat:
    """Tests for get_timing_summary format parameter."""

    @pytest.mark.anyio
    async def test_format_json_default(self, tool_ctx):
        """format='json' (default) returns JSON dict."""
        tool_ctx.timing_log.record("plan", 3.0)
        result = json.loads(await get_timing_summary())
        assert "steps" in result
        assert "total" in result

    @pytest.mark.anyio
    async def test_format_table_returns_markdown(self, tool_ctx):
        """format='table' returns a markdown table string."""
        tool_ctx.timing_log.record("plan", 3.0)
        result = await get_timing_summary(format="table")
        assert "| Step |" in result
        assert "|---" in result
        assert "plan" in result
        assert "**Total**" in result
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


class TestTokenSummaryWallClock:
    @pytest.mark.anyio
    async def test_wall_clock_seconds_merged_from_timing_log(self, tool_ctx):
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()
        tool_ctx.token_log.record(
            "step-a",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=8.0,
        )
        tool_ctx.timing_log.record("step-a", 12.5)

        result = json.loads(await get_token_summary())
        step = next(s for s in result["steps"] if s["step_name"] == "step-a")
        assert step["wall_clock_seconds"] == pytest.approx(12.5)
        assert step["elapsed_seconds"] == pytest.approx(8.0)

    @pytest.mark.anyio
    async def test_wall_clock_falls_back_to_elapsed_when_no_timing(self, tool_ctx):
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()
        tool_ctx.token_log.record(
            "step-b",
            {
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=5.0,
        )

        # Verify no step-b entry was injected into timing_log (guards against parallel pollution)
        assert not any(e["step_name"] == "step-b" for e in tool_ctx.timing_log.get_report())
        result = json.loads(await get_token_summary())
        step = next(s for s in result["steps"] if s["step_name"] == "step-b")
        # No timing_log entry → falls back to elapsed_seconds
        assert step["wall_clock_seconds"] == pytest.approx(5.0)

    @pytest.mark.anyio
    async def test_merge_wall_clock_after_normalization(self, tool_ctx):
        """
        When token entries and timing entries are both normalized to canonical names,
        _merge_wall_clock_seconds must match them correctly.
        """
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()

        # Record via suffixed names — both should normalize to "implement"
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("implement-30", usage)
        tool_ctx.token_log.record("implement-31", usage)
        tool_ctx.timing_log.record("implement-30", 60.0)
        tool_ctx.timing_log.record("implement-31", 55.0)

        result = json.loads(await get_token_summary(clear=False, format="json"))

        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["step_name"] == "implement"
        assert step["input_tokens"] == 200
        # wall_clock_seconds from timing log should be present
        assert "wall_clock_seconds" in step
        assert step["wall_clock_seconds"] == pytest.approx(115.0)


class TestClearMarkerWritten:
    @pytest.mark.anyio
    async def test_token_summary_clear_writes_marker(self, tool_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.session_log import read_telemetry_clear_marker

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        await get_token_summary(clear=True)
        marker = read_telemetry_clear_marker(log_dir)
        assert marker is not None

    @pytest.mark.anyio
    async def test_timing_summary_clear_writes_marker(self, tool_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.session_log import read_telemetry_clear_marker

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        await get_timing_summary(clear=True)
        assert read_telemetry_clear_marker(log_dir) is not None

    @pytest.mark.anyio
    async def test_pipeline_report_clear_writes_marker(self, tool_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.session_log import read_telemetry_clear_marker

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        await get_pipeline_report(clear=True)
        assert read_telemetry_clear_marker(log_dir) is not None


class TestGetLogRoot:
    def test_returns_resolved_log_dir(self, tool_ctx, tmp_path, monkeypatch):
        """_get_log_root() returns the resolved path for the configured log_dir."""
        from autoskillit.server.helpers import resolve_log_dir
        from autoskillit.server.tools_status import _get_log_root

        log_dir = tmp_path / "custom_logs"
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = _get_log_root()
        assert result == resolve_log_dir(str(log_dir))

    def test_returns_path_type(self, tool_ctx, tmp_path, monkeypatch):
        """_get_log_root() returns a Path, not a string."""
        from autoskillit.server.tools_status import _get_log_root

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(tmp_path))
        assert isinstance(_get_log_root(), Path)


@pytest.mark.anyio
async def test_get_token_summary_not_contaminated_by_prior_pipeline(
    tmp_path, tool_ctx, monkeypatch
):
    """
    get_token_summary() must return only the current pipeline's data.
    Entries written by a prior pipeline run must not appear in the summary,
    even if the server was restarted with those sessions present in the log dir.
    """
    from unittest.mock import patch

    from autoskillit.server import _state
    from autoskillit.server._state import _initialize

    # tool_ctx fixture sets log_dir to tmp_path/"session_logs" — write sessions there
    # so _initialize reads from the same directory it is configured to use.
    log_dir = Path(tool_ctx.config.linux_tracing.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prior_cwd = str(tmp_path / "prior-pipeline-clone")
    # Use a current timestamp so it falls within _initialize's 24-hour recovery window
    now_ts = datetime.now(UTC).isoformat()

    # Write sessions from a PRIOR pipeline (different cwd) using direct file writes
    session_dir = log_dir / "sessions" / "sess-prior"
    session_dir.mkdir(parents=True)
    (session_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "step_name": "implement",
                "input_tokens": 9999,
                "output_tokens": 4444,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 120.0,
            }
        )
    )
    with (log_dir / "sessions.jsonl").open("a") as f:
        f.write(
            json.dumps({"dir_name": "sess-prior", "timestamp": now_ts, "cwd": prior_cwd}) + "\n"
        )

    # Simulate server startup with cross-pipeline sessions in the log dir
    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(tool_ctx)
    # Register the _ctx mutation via monkeypatch so teardown is deterministic
    # rather than relying on coincidental identity with the tool_ctx fixture's patch.
    monkeypatch.setattr(_state, "_ctx", tool_ctx)

    # Now simulate the CURRENT pipeline recording its own step
    tool_ctx.token_log.record(
        "rectify",
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    )

    # get_token_summary uses _get_ctx() internally — tool_ctx fixture wires _ctx correctly
    result_json = await get_token_summary(clear=False, format="json")
    result = json.loads(result_json)
    step_names = [s["step_name"] for s in result["steps"]]

    assert "implement" not in step_names, (
        "Prior pipeline step 'implement' must not appear in current pipeline summary; "
        "cross-pipeline contamination detected"
    )
    assert "rectify" in step_names, "Current pipeline step must appear"
    assert len(step_names) == 1, f"Expected only current pipeline steps, got: {step_names}"

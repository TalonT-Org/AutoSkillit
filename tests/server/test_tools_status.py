"""Tests for autoskillit server status tools."""

from __future__ import annotations

import anyio
import json
from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig, TokenUsageConfig
from autoskillit.core.types import ChannelConfirmation
from autoskillit.execution.github import DefaultGitHubFetcher
from autoskillit.pipeline.audit import FailureRecord
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_execution import run_skill
from autoskillit.server.tools_status import (
    get_pipeline_report,
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
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

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


def test_check_quota_absent_from_mcp_registry(tool_ctx):
    """check_quota must not appear in the agent-visible tool list.
    Quota enforcement is the PreToolUse hook's responsibility, not an MCP tool."""
    from autoskillit.server import mcp

    tools = anyio.run(mcp.list_tools)
    assert "check_quota" not in {t.name for t in tools}, (
        "check_quota must be removed from the MCP registry. "
        "Agents should not call it — the PreToolUse hook enforces quota automatically."
    )

"""Tests for MCP response tracking integration in tools_status handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.server.tools_status import get_token_summary, write_telemetry_files

pytestmark = [pytest.mark.layer("server")]


class TestGetTokenSummaryMcpResponses:
    @pytest.mark.anyio
    async def test_includes_mcp_responses_section(self, tool_ctx):
        """get_token_summary returns an mcp_responses key with per-tool data."""
        tool_ctx.response_log.record("run_skill", "a" * 400)

        result = json.loads(await get_token_summary())
        assert "mcp_responses" in result
        assert "steps" in result["mcp_responses"]
        assert "total" in result["mcp_responses"]
        assert result["mcp_responses"]["steps"][0]["tool_name"] == "run_skill"

    @pytest.mark.anyio
    async def test_mcp_responses_steps_contain_expected_fields(self, tool_ctx):
        """Each step in mcp_responses has the expected fields."""
        tool_ctx.response_log.record("load_recipe", "x" * 800)

        result = json.loads(await get_token_summary())
        step = result["mcp_responses"]["steps"][0]
        assert step["tool_name"] == "load_recipe"
        assert step["response_bytes"] == 800
        assert step["estimated_response_tokens"] == 200  # 800 // 4
        assert step["invocation_count"] == 1

    @pytest.mark.anyio
    async def test_mcp_responses_total_fields(self, tool_ctx):
        """mcp_responses.total contains aggregated byte and token counts."""
        tool_ctx.response_log.record("run_skill", "a" * 400)
        tool_ctx.response_log.record("load_recipe", "b" * 800)

        result = json.loads(await get_token_summary())
        total = result["mcp_responses"]["total"]
        assert total["total_response_bytes"] == 1200
        assert total["total_estimated_response_tokens"] == 300
        assert total["total_invocations"] == 2

    @pytest.mark.anyio
    async def test_mcp_responses_empty_when_no_responses_recorded(self, tool_ctx):
        """mcp_responses section returns empty steps when no responses recorded."""
        result = json.loads(await get_token_summary())
        assert result["mcp_responses"]["steps"] == []
        assert result["mcp_responses"]["total"]["total_invocations"] == 0

    @pytest.mark.anyio
    async def test_clear_true_also_clears_response_log(self, tool_ctx):
        """When clear=True, response_log is cleared alongside token_log.

        Note: @track_response_size records the get_token_summary response itself
        after the function body runs (including after clear). The second call may
        have a 'get_token_summary' entry from the decorator, but 'run_skill'
        (recorded before clear) must be absent.
        """
        tool_ctx.response_log.record("run_skill", "data" * 100)

        result = json.loads(await get_token_summary(clear=True))
        names_before = {s["tool_name"] for s in result["mcp_responses"]["steps"]}
        assert "run_skill" in names_before

        result2 = json.loads(await get_token_summary())
        names_after = {s["tool_name"] for s in result2["mcp_responses"]["steps"]}
        assert "run_skill" not in names_after


class TestWriteTelemetryFilesMcpResponse:
    @pytest.mark.anyio
    async def test_writes_mcp_response_metrics_json(self, tool_ctx, tmp_path):
        """write_telemetry_files writes mcp_response_metrics.json alongside token_summary.md."""
        tool_ctx.response_log.record("run_skill", "x" * 800)

        result = json.loads(await write_telemetry_files(str(tmp_path)))

        metrics_file = Path(result["mcp_response_metrics_path"])
        assert metrics_file.exists()
        data = json.loads(metrics_file.read_text())
        assert "steps" in data
        assert "total" in data
        assert data["steps"][0]["tool_name"] == "run_skill"

    @pytest.mark.anyio
    async def test_mcp_response_metrics_path_key_in_result(self, tool_ctx, tmp_path):
        """write_telemetry_files returns mcp_response_metrics_path alongside existing keys."""
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        assert "token_summary_path" in result
        assert "timing_summary_path" in result
        assert "mcp_response_metrics_path" in result

    @pytest.mark.anyio
    async def test_mcp_response_metrics_json_structure(self, tool_ctx, tmp_path):
        """mcp_response_metrics.json has steps list and total dict."""
        tool_ctx.response_log.record("run_skill", "a" * 400)
        tool_ctx.response_log.record("load_recipe", "b" * 800)

        result = json.loads(await write_telemetry_files(str(tmp_path)))
        metrics_path = Path(result["mcp_response_metrics_path"])
        data = json.loads(metrics_path.read_text())

        assert isinstance(data["steps"], list)
        assert len(data["steps"]) == 2
        assert data["total"]["total_response_bytes"] == 1200

    @pytest.mark.anyio
    async def test_existing_keys_unchanged(self, tool_ctx, tmp_path):
        """token_summary_path and timing_summary_path keys remain correct in result."""
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        assert Path(result["token_summary_path"]).name == "token_summary.md"
        assert Path(result["timing_summary_path"]).name == "timing_summary.md"
        assert Path(result["mcp_response_metrics_path"]).name == "mcp_response_metrics.json"

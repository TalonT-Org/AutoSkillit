"""Tests for build_food_truck_cmd — L3 orchestrator session command builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ClaudeFlags, DirectInstall, MarketplaceInstall, OutputFormat
from autoskillit.core.types._type_dispatch_identity import DispatchIdentity
from autoskillit.execution.commands import (
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    ClaudeHeadlessCmd,
    build_food_truck_cmd,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildFoodTruckCmd:
    BASE = dict(
        orchestrator_prompt="You are an L3 food truck orchestrator...",
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        cwd="/repo",
        completion_marker="%%L3_DONE::abc12345%%",
        model=None,
        env_extras=None,
        output_format=OutputFormat.STREAM_JSON,
    )

    def test_returns_claude_headless_cmd(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert isinstance(spec, ClaudeHeadlessCmd)

    def test_cmd_starts_with_claude(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.cmd[0] == "claude"

    def test_env_has_session_type_orchestrator(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"

    def test_env_has_autoskillit_headless(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_env_has_max_mcp_output_tokens(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_env_has_mcp_connection_nonblocking(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["MCP_CONNECTION_NONBLOCKING"] == "0"

    def test_does_not_call_ensure_skill_prefix(self):
        """Prompt passed through verbatim — no 'Use ' prefix injected."""
        spec = build_food_truck_cmd(**self.BASE)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        prompt = spec.cmd[prompt_idx]
        assert not prompt.startswith("Use ")
        assert "You are an L3 food truck orchestrator" in prompt

    def test_tools_flag_restricts_to_ask_user_question(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert ClaudeFlags.TOOLS in spec.cmd
        idx = spec.cmd.index(ClaudeFlags.TOOLS)
        assert spec.cmd[idx + 1] == "AskUserQuestion"

    def test_plugin_dir_present(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert ClaudeFlags.PLUGIN_DIR in spec.cmd
        idx = spec.cmd.index(ClaudeFlags.PLUGIN_DIR)
        assert spec.cmd[idx + 1] == "/plugins"

    def test_build_food_truck_cmd_marketplace_uses_cache_path(self, tmp_path: Path):
        """build_food_truck_cmd with MarketplaceInstall uses cache_path for --plugin-dir."""
        cache = tmp_path / "marketplace_cache"
        cache.mkdir()
        cmd = build_food_truck_cmd(
            **{**self.BASE, "plugin_source": MarketplaceInstall(cache_path=cache)}
        )
        idx = cmd.cmd.index("--plugin-dir")
        assert cmd.cmd[idx + 1] == str(cache)

    def test_build_food_truck_cmd_direct_uses_plugin_dir(self, tmp_path: Path):
        """build_food_truck_cmd with DirectInstall uses plugin_dir for --plugin-dir."""
        cmd = build_food_truck_cmd(
            **{**self.BASE, "plugin_source": DirectInstall(plugin_dir=tmp_path)}
        )
        idx = cmd.cmd.index("--plugin-dir")
        assert cmd.cmd[idx + 1] == str(tmp_path)

    def test_output_format_present(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert ClaudeFlags.OUTPUT_FORMAT in spec.cmd
        idx = spec.cmd.index(ClaudeFlags.OUTPUT_FORMAT)
        assert spec.cmd[idx + 1] == "stream-json"

    def test_output_format_required_flags_appended(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert "--verbose" in spec.cmd

    def test_output_format_required_flags_not_duplicated(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.cmd.count("--verbose") == 1

    def test_completion_marker_in_prompt(self):
        spec = build_food_truck_cmd(**self.BASE)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        assert "%%L3_DONE::abc12345%%" in spec.cmd[prompt_idx]

    def test_cwd_anchor_in_prompt(self):
        spec = build_food_truck_cmd(**self.BASE)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        assert "/repo" in spec.cmd[prompt_idx]

    def test_narration_suppression_in_prompt(self):
        spec = build_food_truck_cmd(**self.BASE)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        assert "EFFICIENCY DIRECTIVE" in spec.cmd[prompt_idx]

    def test_env_extras_layered(self):
        params = {**self.BASE, "env_extras": {"AUTOSKILLIT_CAMPAIGN_ID": "camp-1"}}
        spec = build_food_truck_cmd(**params)
        assert spec.env["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-1"

    def test_env_extras_do_not_override_session_type(self):
        params = {**self.BASE, "env_extras": {"AUTOSKILLIT_SESSION_TYPE": "skill"}}
        spec = build_food_truck_cmd(**params)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"

    def test_env_overrides_ambient_session_type(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"

    def test_private_vars_scrubbed_from_host_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", "/tmp/state")
        monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "kitchen")
        monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", "/tmp/proj")
        spec = build_food_truck_cmd(**self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in spec.env
        assert "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS" not in spec.env
        assert "AUTOSKILLIT_PROJECT_DIR" not in spec.env

    def test_model_injected_when_provided(self):
        params = {**self.BASE, "model": "claude-opus-4-6"}
        spec = build_food_truck_cmd(**params)
        assert ClaudeFlags.MODEL in spec.cmd
        idx = spec.cmd.index(ClaudeFlags.MODEL)
        assert spec.cmd[idx + 1] == "claude-opus-4-6"

    def test_model_omitted_when_none(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert ClaudeFlags.MODEL not in spec.cmd

    def test_env_strips_sse_port(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        spec = build_food_truck_cmd(**self.BASE)
        assert "CLAUDE_CODE_SSE_PORT" not in spec.env

    def test_no_first_action_in_food_truck(self):
        spec = build_food_truck_cmd(
            orchestrator_prompt="Run the campaign",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "FIRST ACTION" not in prompt
        assert "After loading" not in prompt


class TestBuildFoodTruckCmdPackTags:
    def test_env_extras_with_l3_tool_tags_passes_through(self):
        """env_extras containing AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS reaches subprocess env."""
        spec = build_food_truck_cmd(
            orchestrator_prompt="...",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="%%DONE%%",
            env_extras={"AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "github,ci,clone,telemetry"},
        )
        assert spec.env["AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"] == "github,ci,clone,telemetry"


class TestBuildFoodTruckCmdFeatureParity:
    """Tests for features ported from build_skill_session_cmd (issue #1656)."""

    BASE = dict(
        orchestrator_prompt="You are an L3 food truck orchestrator...",
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        cwd="/repo",
        completion_marker="%%L3_DONE::abc12345%%",
        model=None,
        env_extras=None,
        output_format=OutputFormat.STREAM_JSON,
        exit_after_stop_delay_ms=0,
        scenario_step_name="",
        temp_dir_relpath=None,
        allowed_write_prefix="",
    )

    def test_env_has_exit_delay_when_positive(self):
        spec = build_food_truck_cmd(**{**self.BASE, "exit_after_stop_delay_ms": 2000})
        assert spec.env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    def test_env_omits_exit_delay_when_zero(self):
        spec = build_food_truck_cmd(**{**self.BASE, "exit_after_stop_delay_ms": 0})
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_env_has_stream_idle_timeout_when_positive(self):
        spec = build_food_truck_cmd(**{**self.BASE, "stream_idle_timeout_ms": 120000})
        assert spec.env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] == "120000"

    def test_env_omits_stream_idle_timeout_when_zero(self):
        spec = build_food_truck_cmd(**{**self.BASE, "stream_idle_timeout_ms": 0})
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_headless_exclusive_vars_stripped_stream_idle_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_STREAM_IDLE_TIMEOUT_MS in host env must be stripped even when ms=0."""
        monkeypatch.setenv("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "99999")
        spec = build_food_truck_cmd(**{**self.BASE, "stream_idle_timeout_ms": 0})
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_env_has_scenario_step_name_when_set(self):
        spec = build_food_truck_cmd(**{**self.BASE, "scenario_step_name": "cook-recipe"})
        assert spec.env["SCENARIO_STEP_NAME"] == "cook-recipe"

    def test_env_omits_scenario_step_name_when_empty(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert "SCENARIO_STEP_NAME" not in spec.env

    def test_env_forwards_kitchen_session_id(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_KITCHEN_SESSION_ID", "ks-abc")
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_KITCHEN_SESSION_ID"] == "ks-abc"

    def test_env_omits_kitchen_session_id_when_absent(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        spec = build_food_truck_cmd(**self.BASE)
        assert "AUTOSKILLIT_KITCHEN_SESSION_ID" not in spec.env

    def test_allowed_write_prefix_in_env(self):
        spec = build_food_truck_cmd(**{**self.BASE, "allowed_write_prefix": "/tmp/foo/"})
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/foo/"

    def test_allowed_write_prefix_absent_when_empty(self):
        spec = build_food_truck_cmd(**{**self.BASE, "allowed_write_prefix": ""})
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in spec.env

    def test_allowed_write_prefix_exclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "old")
        spec = build_food_truck_cmd(**{**self.BASE, "allowed_write_prefix": "new"})
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "new"

    def test_temp_dir_relpath_in_prompt(self):
        spec = build_food_truck_cmd(**{**self.BASE, "temp_dir_relpath": ".autoskillit/temp"})
        prompt_text = spec.cmd[2]
        assert ".autoskillit/temp" in prompt_text

    def test_headless_exclusive_vars_stripped_exit_delay(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "99999")
        spec = build_food_truck_cmd(**{**self.BASE, "exit_after_stop_delay_ms": 0})
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_headless_exclusive_vars_stripped_scenario_step(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SCENARIO_STEP_NAME", "outer-step")
        spec = build_food_truck_cmd(**{**self.BASE, "scenario_step_name": ""})
        assert "SCENARIO_STEP_NAME" not in spec.env


class TestBuildFoodTruckCmdResume:
    BASE = dict(
        orchestrator_prompt="You are an L3 food truck orchestrator...",
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        cwd="/repo",
        completion_marker="%%L3_DONE::abc12345%%",
    )

    def test_resume_session_id_adds_resume_flag(self):
        spec = build_food_truck_cmd(**self.BASE, resume_session_id="abc-123")
        assert "--resume" in spec.cmd
        idx = spec.cmd.index("--resume")
        assert spec.cmd[idx + 1] == "abc-123"

    def test_no_resume_session_id_omits_resume_flag(self):
        spec = build_food_truck_cmd(**self.BASE)
        assert "--resume" not in spec.cmd

    def test_none_resume_session_id_omits_resume_flag(self):
        spec = build_food_truck_cmd(**self.BASE, resume_session_id=None)
        assert "--resume" not in spec.cmd

    @pytest.mark.parametrize(
        "attr",
        ["sentinel_open", "sentinel_close", "completion_marker"],
    )
    def test_resume_prompt_contains_sentinel_markers(self, attr: str):
        """Resume prompt must contain all sentinel markers from DispatchIdentity."""
        identity = DispatchIdentity.from_dispatch_id("aaaabbbb-cccc-dddd-eeee-ffffffffffff")
        spec = build_food_truck_cmd(
            **self.BASE, resume_session_id="abc-123", sentinel_contract=identity.sentinel_contract
        )
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert getattr(identity, attr) in prompt

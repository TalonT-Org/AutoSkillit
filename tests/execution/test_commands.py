"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BareResume,
    ClaudeFlags,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    NoResume,
)
from autoskillit.execution.commands import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_food_truck_cmd,
    build_headless_cmd,
    build_headless_resume_cmd,
    build_interactive_cmd,
    build_leaf_headless_cmd,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildInteractiveCmd:
    def test_returns_correct_type(self) -> None:
        result = build_interactive_cmd()
        assert isinstance(result, ClaudeInteractiveCmd)

    def test_includes_dangerously_skip_permissions(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd

    def test_does_not_include_allow_dangerously_skip_permissions(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd

    def test_does_not_include_prompt_flag(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.PRINT not in result.cmd

    def test_starts_with_claude(self) -> None:
        result = build_interactive_cmd()
        assert result.cmd[0] == "claude"

    def test_env_is_populated_and_scrubbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        monkeypatch.setenv("HOME", "/tmp/home")
        result = build_interactive_cmd()
        assert "CLAUDE_CODE_SSE_PORT" not in result.env
        assert result.env.get("HOME") == "/tmp/home"
        assert result.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_accepts_model(self) -> None:
        result = build_interactive_cmd(model="claude-opus-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-opus-4-6"

    def test_no_model_flag_when_model_is_none(self) -> None:
        result = build_interactive_cmd(model=None)
        assert ClaudeFlags.MODEL not in result.cmd

    def test_includes_initial_prompt_as_positional_arg(self) -> None:
        result = build_interactive_cmd(initial_prompt="Hello chef")
        assert "Hello chef" in result.cmd
        assert ClaudeFlags.PRINT not in result.cmd  # still interactive, not headless

    def test_omits_prompt_when_initial_prompt_is_none(self) -> None:
        result = build_interactive_cmd()
        # cmd is just ["claude", "--dangerously-skip-permissions"]
        assert len(result.cmd) == 2

    # REQ-CMD-001
    def test_named_resume_appends_session_id(self) -> None:
        result = build_interactive_cmd(resume_spec=NamedResume(session_id="abc123"))
        assert "--resume" in result.cmd
        idx = result.cmd.index("--resume")
        assert result.cmd[idx + 1] == "abc123"

    def test_bare_resume_produces_bare_flag_no_id(self) -> None:
        result = build_interactive_cmd(resume_spec=BareResume())
        assert "--resume" in result.cmd
        idx = result.cmd.index("--resume")
        assert idx == len(result.cmd) - 1

    def test_no_resume_spec_emits_no_flag(self) -> None:
        result = build_interactive_cmd(resume_spec=NoResume())
        assert "--resume" not in result.cmd

    def test_resume_placed_before_initial_prompt(self) -> None:
        result = build_interactive_cmd(
            resume_spec=NamedResume(session_id="abc123"), initial_prompt="hello"
        )
        resume_idx = result.cmd.index("--resume")
        prompt_idx = result.cmd.index("hello")
        assert resume_idx < prompt_idx

    def test_env_has_max_mcp_output_tokens(self) -> None:
        """build_interactive_cmd must inject MAX_MCP_OUTPUT_TOKENS even with no env_extras."""
        spec = build_interactive_cmd()
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_caller_extras_override_baseline(self) -> None:
        """Caller-supplied env_extras must override the baseline default."""
        spec = build_interactive_cmd(env_extras={"MAX_MCP_OUTPUT_TOKENS": "99999"})
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == "99999"


class TestBuildInteractiveCmdExtended:
    def test_accepts_plugin_source_direct_install(self, tmp_path: Path) -> None:
        """build_interactive_cmd with DirectInstall includes --plugin-dir flag."""
        plugin_source = DirectInstall(plugin_dir=tmp_path)
        result = build_interactive_cmd(plugin_source=plugin_source)
        assert "--plugin-dir" in result.cmd
        idx = result.cmd.index("--plugin-dir")
        assert result.cmd[idx + 1] == str(tmp_path)

    def test_accepts_add_dirs(self, tmp_path: Path) -> None:
        """build_interactive_cmd with add_dirs includes --add-dir for each entry."""
        d1, d2 = Path(tmp_path) / "a", Path(tmp_path) / "b"
        result = build_interactive_cmd(add_dirs=[d1, d2])
        assert result.cmd.count("--add-dir") == 2

    def test_marketplace_install_omits_plugin_dir_flag(self, tmp_path: Path) -> None:
        """build_interactive_cmd with MarketplaceInstall does not emit --plugin-dir."""
        result = build_interactive_cmd(plugin_source=MarketplaceInstall(cache_path=tmp_path))
        assert "--plugin-dir" not in result.cmd

    def test_no_plugin_source_omits_plugin_dir_flag(self) -> None:
        """build_interactive_cmd with no plugin_source does not emit --plugin-dir."""
        result = build_interactive_cmd()
        assert "--plugin-dir" not in result.cmd

    def test_cook_uses_builder_output(self, tmp_path: Path) -> None:
        """cook subprocess cmd is consistent with build_interactive_cmd output."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import pkg_root

        fake_skills_dir = Path(tmp_path) / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("sys.stdin.isatty", return_value=True),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        actual_cmd = mock_run.call_args[0][0]
        expected_prefix = build_interactive_cmd(
            plugin_source=DirectInstall(plugin_dir=pkg_root()), add_dirs=[fake_skills_dir]
        ).cmd
        assert actual_cmd == expected_prefix


class TestBuildHeadlessCmd:
    def test_returns_correct_type(self) -> None:
        result = build_headless_cmd("some prompt")
        assert isinstance(result, ClaudeHeadlessCmd)

    def test_includes_prompt_flag(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.PRINT in result.cmd

    def test_includes_dangerously_skip_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd

    def test_does_not_include_allow_dangerously_skip_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd

    def test_env_is_populated_and_scrubbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        monkeypatch.setenv("HOME", "/tmp/home")
        result = build_headless_cmd("some prompt")
        assert "CLAUDE_CODE_SSE_PORT" not in result.env
        assert result.env.get("HOME") == "/tmp/home"
        assert result.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_accepts_model(self) -> None:
        result = build_headless_cmd("some prompt", model="claude-sonnet-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-sonnet-4-6"


class TestBuildHeadlessResumeCmd:
    def test_basic_cmd_structure(self) -> None:
        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert result.cmd[0] == "claude"
        assert ClaudeFlags.PRINT in result.cmd
        assert ClaudeFlags.RESUME in result.cmd
        assert result.cmd[result.cmd.index(ClaudeFlags.RESUME) + 1] == "abc-123"
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd
        assert ClaudeFlags.OUTPUT_FORMAT in result.cmd
        assert result.cmd[result.cmd.index(ClaudeFlags.OUTPUT_FORMAT) + 1] == "json"
        prompt_idx = result.cmd.index(ClaudeFlags.PRINT) + 1
        assert result.cmd[prompt_idx] == "Emit token"

    def test_env_is_populated_with_ide_suppression(self) -> None:
        from collections.abc import Mapping

        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert isinstance(result.env, Mapping)
        assert len(result.env) > 0
        assert result.env.get("CLAUDE_CODE_AUTO_CONNECT_IDE") == "0"

    def test_env_has_max_mcp_output_tokens(self) -> None:
        """build_headless_resume_cmd must inject MAX_MCP_OUTPUT_TOKENS even with no env_extras."""
        spec = build_headless_resume_cmd(resume_session_id="abc", prompt="Emit token")
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_no_plugin_dir_by_default(self) -> None:
        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert ClaudeFlags.PLUGIN_DIR not in result.cmd

    def test_with_plugin_source_direct_install(self) -> None:
        result = build_headless_resume_cmd(
            resume_session_id="abc-123",
            prompt="Emit token",
            plugin_source=DirectInstall(plugin_dir=Path("/tmp/plugin")),
        )
        assert ClaudeFlags.PLUGIN_DIR in result.cmd
        idx = result.cmd.index(ClaudeFlags.PLUGIN_DIR)
        assert result.cmd[idx + 1] == "/tmp/plugin"


class TestBuildLeafHeadlessCmd:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )

    def test_returns_claude_headless_cmd(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert isinstance(spec, ClaudeHeadlessCmd)

    def test_cmd_starts_with_claude_not_env(self):
        """Argv no longer carries a leading ['env', ...] prefix."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.cmd[0] == "claude"
        assert "env" != spec.cmd[0]
        assert not any(tok.startswith("AUTOSKILLIT_HEADLESS=") for tok in spec.cmd)
        assert not any(tok.startswith("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=") for tok in spec.cmd)
        assert not any(tok.startswith("SCENARIO_STEP_NAME=") for tok in spec.cmd)

    def test_env_has_autoskillit_headless(self):
        """AUTOSKILLIT_HEADLESS=1 now lives on spec.env, not in argv."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_env_has_exit_delay_when_positive(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    def test_env_omits_exit_delay_when_zero(self):
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_env_strips_sse_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "CLAUDE_CODE_SSE_PORT" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_exit_delay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_CODE_EXIT_AFTER_STOP_DELAY in host env must be stripped even when ms=0."""
        monkeypatch.setenv("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "99999")
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_scenario_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCENARIO_STEP_NAME in host env must be stripped even when no step name is given."""
        monkeypatch.setenv("SCENARIO_STEP_NAME", "outer-step")
        params = {**self.BASE, "scenario_step_name": ""}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "SCENARIO_STEP_NAME" not in spec.env

    def test_env_has_auto_connect_off(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_plugin_source_direct_install_present(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "--plugin-dir" in spec.cmd
        idx = spec.cmd.index("--plugin-dir")
        assert spec.cmd[idx + 1] == "/plugins"

    def test_marketplace_install_omits_plugin_dir(self, tmp_path: Path):
        params = {**self.BASE, "plugin_source": MarketplaceInstall(cache_path=tmp_path)}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "--plugin-dir" not in spec.cmd

    def test_output_format_present(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "--output-format" in spec.cmd
        idx = spec.cmd.index("--output-format")
        assert spec.cmd[idx + 1] == "stream-json"

    def test_output_format_required_flags_appended(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "--verbose" in spec.cmd

    def test_output_format_required_flags_not_duplicated(self):
        """If a required flag is already present it must not be added twice."""
        params = {**self.BASE, "output_format_required_flags": ["--output-format"]}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert spec.cmd.count("--output-format") == 1

    def test_add_dirs_injected(self):
        from autoskillit.core import ValidatedAddDir

        d = ValidatedAddDir(path="/skills/custom")
        params = {**self.BASE, "add_dirs": [d]}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "--add-dir" in spec.cmd
        idx = spec.cmd.index("--add-dir")
        assert spec.cmd[idx + 1] == "/skills/custom"

    def test_no_add_dirs_emits_no_flag(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_skill_prefix_injected(self):
        """Slash commands must be prefixed with 'Use '."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert cmd[prompt_idx].startswith("Use /investigate")

    def test_completion_marker_appended(self):
        """Completion directive must appear in the prompt."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "DONE" in cmd[prompt_idx]

    def test_cwd_anchor_appended(self):
        """Working-directory anchor must appear in the prompt."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "/repo" in cmd[prompt_idx]

    def test_model_injected_when_provided(self):
        params = {**self.BASE, "model": "claude-opus-4-6"}
        spec = build_leaf_headless_cmd("/investigate foo", **params)
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "claude-opus-4-6"

    def test_model_omitted_when_none(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "--model" not in spec.cmd

    def test_narration_suppression_directive_in_prompt(self):
        """EFFICIENCY DIRECTIVE must appear in the assembled prompt."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "EFFICIENCY DIRECTIVE" in cmd[prompt_idx]

    def test_env_has_max_mcp_output_tokens(self):
        """MAX_MCP_OUTPUT_TOKENS=50000 must be present in headless session env."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_max_mcp_output_tokens_not_in_argv(self):
        """MAX_MCP_OUTPUT_TOKENS must live in spec.env, not in argv."""
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert not any(tok.startswith("MAX_MCP_OUTPUT_TOKENS=") for tok in spec.cmd)

    def test_headless_exclusive_vars_strips_host_max_mcp_output_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Host-env MAX_MCP_OUTPUT_TOKENS must be stripped and replaced by the hardcoded value."""
        monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "99999")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_env_has_session_type_leaf(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "leaf"

    def test_env_overrides_ambient_session_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "leaf"

    def test_env_forwards_campaign_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-42")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-42"

    def test_env_omits_campaign_id_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_ID" not in spec.env

    def test_env_forwards_kitchen_session_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T32 — AUTOSKILLIT_KITCHEN_SESSION_ID forwarded into spec.env when set."""
        monkeypatch.setenv("AUTOSKILLIT_KITCHEN_SESSION_ID", "kit-77")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_KITCHEN_SESSION_ID"] == "kit-77"

    def test_env_omits_kitchen_session_id_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T33 — AUTOSKILLIT_KITCHEN_SESSION_ID absent from spec.env when not set."""
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_KITCHEN_SESSION_ID" not in spec.env

    def test_private_vars_scrubbed_except_explicit_forwards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", "/tmp/state")
        monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", "/tmp/proj")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "kitchen")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in spec.env
        assert "AUTOSKILLIT_PROJECT_DIR" not in spec.env
        assert "AUTOSKILLIT_L2_TOOL_TAGS" not in spec.env


class TestBuildFoodTruckCmd:
    BASE = dict(
        orchestrator_prompt="You are an L2 food truck orchestrator...",
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        cwd="/repo",
        completion_marker="%%L2_DONE::abc12345%%",
        model=None,
        env_extras=None,
        output_format_value="stream-json",
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
        assert "You are an L2 food truck orchestrator" in prompt

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

    def test_completion_marker_in_prompt(self):
        spec = build_food_truck_cmd(**self.BASE)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        assert "%%L2_DONE::abc12345%%" in spec.cmd[prompt_idx]

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
        params = {**self.BASE, "env_extras": {"AUTOSKILLIT_SESSION_TYPE": "leaf"}}
        spec = build_food_truck_cmd(**params)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"

    def test_env_overrides_ambient_session_type(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        spec = build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"

    def test_private_vars_scrubbed_from_host_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", "/tmp/state")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "kitchen")
        monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", "/tmp/proj")
        spec = build_food_truck_cmd(**self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in spec.env
        assert "AUTOSKILLIT_L2_TOOL_TAGS" not in spec.env
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


class TestBuildFoodTruckCmdPackTags:
    def test_env_extras_with_l2_tool_tags_passes_through(self):
        """env_extras containing AUTOSKILLIT_L2_TOOL_TAGS reaches subprocess env."""
        spec = build_food_truck_cmd(
            orchestrator_prompt="...",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="%%DONE%%",
            env_extras={"AUTOSKILLIT_L2_TOOL_TAGS": "github,ci,clone,telemetry"},
        )
        assert spec.env["AUTOSKILLIT_L2_TOOL_TAGS"] == "github,ci,clone,telemetry"


def test_headless_exclusive_vars_contains_max_mcp_output_tokens() -> None:
    """MAX_MCP_OUTPUT_TOKENS must be in _HEADLESS_EXCLUSIVE_VARS."""
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    assert "MAX_MCP_OUTPUT_TOKENS" in _HEADLESS_EXCLUSIVE_VARS


# MAINTENANCE: When adding a new session builder to commands.py,
# add it to this parametrize list. test_no_raw_claude_env ensures
# env routing; this test ensures env CONTENT.
@pytest.mark.parametrize(
    "builder_call",
    [
        lambda: build_interactive_cmd(),
        lambda: build_leaf_headless_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format_value="stream-json",
        ),
        lambda: build_headless_resume_cmd(resume_session_id="abc", prompt="Emit"),
        lambda: build_food_truck_cmd(
            orchestrator_prompt="You are an L2 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
    ],
    ids=["interactive", "leaf_headless", "headless_resume", "food_truck"],
)
def test_all_session_builders_inject_max_mcp_output_tokens(builder_call) -> None:
    """Every session command builder must produce env with MAX_MCP_OUTPUT_TOKENS."""
    spec = builder_call()
    assert "MAX_MCP_OUTPUT_TOKENS" in spec.env
    assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE


def test_session_baseline_env_contains_mcp_connection_nonblocking() -> None:
    from autoskillit.execution.commands import _SESSION_BASELINE_ENV

    assert "MCP_CONNECTION_NONBLOCKING" in _SESSION_BASELINE_ENV
    assert _SESSION_BASELINE_ENV["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_interactive_cmd_env_has_mcp_connection_nonblocking() -> None:
    spec = build_interactive_cmd()
    assert spec.env.get("MCP_CONNECTION_NONBLOCKING") == "0"


@pytest.mark.parametrize(
    "builder_call",
    [
        lambda: build_interactive_cmd(),
        lambda: build_leaf_headless_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format_value="stream-json",
        ),
        lambda: build_headless_resume_cmd(resume_session_id="abc", prompt="Emit"),
        lambda: build_food_truck_cmd(
            orchestrator_prompt="You are an L2 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
    ],
    ids=["interactive", "leaf_headless", "headless_resume", "food_truck"],
)
def test_all_session_builders_inject_mcp_connection_nonblocking(builder_call) -> None:
    """Every session command builder must produce env with MCP_CONNECTION_NONBLOCKING=0."""
    spec = builder_call()
    assert "MCP_CONNECTION_NONBLOCKING" in spec.env
    assert spec.env["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_launch_id_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_LAUNCH_ID" in _HEADLESS_EXCLUSIVE_VARS


def test_allowed_write_prefix_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" in _HEADLESS_EXCLUSIVE_VARS


def test_skill_name_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_SKILL_NAME" in _HEADLESS_EXCLUSIVE_VARS


def test_leaf_cmd_includes_skill_name() -> None:
    spec = build_leaf_headless_cmd(
        "/autoskillit:planner-analyze some task",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "planner-analyze"


def test_leaf_cmd_skill_name_strips_namespace() -> None:
    spec = build_leaf_headless_cmd(
        "/autoskillit:investigate some issue",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "investigate"


def test_leaf_cmd_skill_name_empty_for_non_slash() -> None:
    spec = build_leaf_headless_cmd(
        "some prompt without slash",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == ""


class TestBuildLeafAllowedWritePrefix:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )

    def test_allowed_write_prefix_in_env(self):
        spec = build_leaf_headless_cmd(
            "/investigate foo", **self.BASE, allowed_write_prefix="/tmp/foo/"
        )
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/foo/"

    def test_allowed_write_prefix_absent_when_empty(self):
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE, allowed_write_prefix="")
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in spec.env

    def test_allowed_write_prefix_exclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "old")
        spec = build_leaf_headless_cmd("/investigate foo", **self.BASE, allowed_write_prefix="new")
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "new"

"""Tests for build_skill_session_cmd — skill session command builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ClaudeFlags, CmdSpec, DirectInstall, MarketplaceInstall, OutputFormat
from autoskillit.execution.commands import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    ClaudeHeadlessCmd,
    build_skill_session_cmd,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildSkillSessionCmd:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )

    def test_returns_claude_headless_cmd(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert isinstance(spec, ClaudeHeadlessCmd)

    def test_cmd_starts_with_claude_not_env(self):
        """Argv no longer carries a leading ['env', ...] prefix."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.cmd[0] == "claude"
        assert "env" != spec.cmd[0]
        assert not any(tok.startswith("AUTOSKILLIT_HEADLESS=") for tok in spec.cmd)
        assert not any(tok.startswith("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=") for tok in spec.cmd)
        assert not any(tok.startswith("SCENARIO_STEP_NAME=") for tok in spec.cmd)

    def test_env_has_autoskillit_headless(self):
        """AUTOSKILLIT_HEADLESS=1 now lives on spec.env, not in argv."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_env_has_exit_delay_when_positive(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    def test_env_omits_exit_delay_when_zero(self):
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_env_has_stream_idle_timeout_when_positive(self):
        params = {**self.BASE, "stream_idle_timeout_ms": 120000}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert spec.env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] == "120000"

    def test_env_omits_stream_idle_timeout_when_zero(self):
        params = {**self.BASE, "stream_idle_timeout_ms": 0}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_headless_exclusive_vars_stripped_stream_idle_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_STREAM_IDLE_TIMEOUT_MS in host env must be stripped even when ms=0."""
        monkeypatch.setenv("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "99999")
        params = {**self.BASE, "stream_idle_timeout_ms": 0}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_env_strips_sse_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "CLAUDE_CODE_SSE_PORT" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_exit_delay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_CODE_EXIT_AFTER_STOP_DELAY in host env must be stripped even when ms=0."""
        monkeypatch.setenv("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "99999")
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_scenario_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCENARIO_STEP_NAME in host env must be stripped even when no step name is given."""
        monkeypatch.setenv("SCENARIO_STEP_NAME", "outer-step")
        params = {**self.BASE, "scenario_step_name": ""}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "SCENARIO_STEP_NAME" not in spec.env

    def test_env_has_auto_connect_off(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_plugin_source_direct_install_present(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "--plugin-dir" in spec.cmd
        idx = spec.cmd.index("--plugin-dir")
        assert spec.cmd[idx + 1] == "/plugins"

    def test_marketplace_install_omits_plugin_dir(self, tmp_path: Path):
        params = {**self.BASE, "plugin_source": MarketplaceInstall(cache_path=tmp_path)}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "--plugin-dir" not in spec.cmd

    def test_output_format_present(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "--output-format" in spec.cmd
        idx = spec.cmd.index("--output-format")
        assert spec.cmd[idx + 1] == "stream-json"

    def test_output_format_required_flags_appended(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "--verbose" in spec.cmd

    def test_output_format_required_flags_not_duplicated(self):
        """Required flags must not appear twice even if already present."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.cmd.count("--verbose") == 1

    def test_add_dirs_injected(self):
        from autoskillit.core import ValidatedAddDir

        d = ValidatedAddDir(path="/skills/custom")
        params = {**self.BASE, "add_dirs": [d]}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "--add-dir" in spec.cmd
        idx = spec.cmd.index("--add-dir")
        assert spec.cmd[idx + 1] == "/skills/custom"

    def test_no_add_dirs_emits_no_flag(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_skill_prefix_injected(self):
        """Slash commands must be prefixed with 'Use the ... skill'."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert cmd[prompt_idx].startswith("Use the /investigate skill")

    def test_completion_marker_appended(self):
        """Completion directive must appear in the prompt."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "DONE" in cmd[prompt_idx]

    def test_completion_marker_is_last_instruction_in_prompt(self):
        """The completion marker value must appear in the final line of the assembled prompt."""
        params = {
            **self.BASE,
            "completion_marker": "%%ORDER_UP::abc12345%%",
            "profile_name": "minimax",
        }
        spec = build_skill_session_cmd("/autoskillit:make-plan arg", **params)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        prompt = spec.cmd[prompt_idx]
        last_block = prompt.split("\n\n")[-1]
        assert "%%ORDER_UP::abc12345%%" in last_block

    def test_completion_reminder_follows_efficiency_directive(self):
        """The end-of-prompt marker reminder must be the last directive."""
        params = {
            **self.BASE,
            "completion_marker": "%%ORDER_UP::abc12345%%",
            "profile_name": "minimax",
        }
        spec = build_skill_session_cmd("/autoskillit:make-plan arg", **params)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        prompt = spec.cmd[prompt_idx]
        eff_pos = prompt.index("EFFICIENCY DIRECTIVE")
        reminder_pos = prompt.index("Remember: end your final response with")
        assert reminder_pos > eff_pos

    def test_cwd_anchor_appended(self):
        """Working-directory anchor must appear in the prompt."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "/repo" in cmd[prompt_idx]

    def test_model_injected_when_provided(self):
        params = {**self.BASE, "model": "claude-opus-4-6"}
        spec = build_skill_session_cmd("/investigate foo", **params)
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "claude-opus-4-6"

    def test_model_omitted_when_none(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "--model" not in spec.cmd

    def test_narration_suppression_directive_in_prompt(self):
        """EFFICIENCY DIRECTIVE must appear in the assembled prompt."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "EFFICIENCY DIRECTIVE" in cmd[prompt_idx]

    def test_env_has_max_mcp_output_tokens(self):
        """MAX_MCP_OUTPUT_TOKENS=50000 must be present in headless session env."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_max_mcp_output_tokens_not_in_argv(self):
        """MAX_MCP_OUTPUT_TOKENS must live in spec.env, not in argv."""
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert not any(tok.startswith("MAX_MCP_OUTPUT_TOKENS=") for tok in spec.cmd)

    def test_headless_exclusive_vars_strips_host_max_mcp_output_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Host-env MAX_MCP_OUTPUT_TOKENS must be stripped and replaced by the hardcoded value."""
        monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "99999")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_env_has_session_type_skill(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "skill"

    def test_env_overrides_ambient_session_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "skill"

    def test_env_forwards_campaign_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-42")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-42"

    def test_env_omits_campaign_id_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_ID" not in spec.env

    def test_env_forwards_kitchen_session_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T32 — AUTOSKILLIT_KITCHEN_SESSION_ID forwarded into spec.env when set."""
        monkeypatch.setenv("AUTOSKILLIT_KITCHEN_SESSION_ID", "kit-77")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_KITCHEN_SESSION_ID"] == "kit-77"

    def test_env_omits_kitchen_session_id_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T33 — AUTOSKILLIT_KITCHEN_SESSION_ID absent from spec.env when not set."""
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_KITCHEN_SESSION_ID" not in spec.env

    def test_private_vars_scrubbed_except_explicit_forwards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", "/tmp/state")
        monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", "/tmp/proj")
        monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "kitchen")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in spec.env
        assert "AUTOSKILLIT_PROJECT_DIR" not in spec.env
        assert "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS" not in spec.env

    def test_provider_extras_injected_into_env(self) -> None:
        spec = build_skill_session_cmd(
            "/investigate foo",
            **self.BASE,
            provider_extras={
                "ANTHROPIC_BASE_URL": "https://custom.example.com",
                "ANTHROPIC_API_KEY": "sk-test",
            },
        )
        assert spec.env["ANTHROPIC_BASE_URL"] == "https://custom.example.com"
        assert spec.env["ANTHROPIC_API_KEY"] == "sk-test"

    def test_provider_extras_cannot_override_session_type(self) -> None:
        spec = build_skill_session_cmd(
            "/investigate foo",
            **self.BASE,
            provider_extras={"AUTOSKILLIT_SESSION_TYPE": "franchise"},
        )
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "skill"

    def test_provider_extras_cannot_override_headless(self) -> None:
        spec = build_skill_session_cmd(
            "/investigate foo",
            **self.BASE,
            provider_extras={"AUTOSKILLIT_HEADLESS": "0"},
        )
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_host_anthropic_base_url_stripped_when_in_exclusive_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Depends on P2-A1 (#1751) having added ANTHROPIC_BASE_URL to
        # _HEADLESS_EXCLUSIVE_VARS.
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://host.example.com")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        assert "ANTHROPIC_BASE_URL" not in spec.env

    def test_provider_extras_none_changes_nothing(self) -> None:
        baseline = build_skill_session_cmd("/investigate foo", **self.BASE)
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, provider_extras=None)
        assert spec.env == baseline.env

    def test_profile_name_injects_provider_profile_env_var(self) -> None:
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, profile_name="minimax")
        assert spec.env["AUTOSKILLIT_PROVIDER_PROFILE"] == "minimax"

    def test_empty_profile_name_omits_provider_profile(self) -> None:
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, profile_name="")
        assert "AUTOSKILLIT_PROVIDER_PROFILE" not in spec.env

    def test_no_first_action_without_profile_name(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE)
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "FIRST ACTION" not in prompt
        assert "After loading" not in prompt

    def test_first_action_with_profile_name(self):
        spec = build_skill_session_cmd(
            "/autoskillit:investigate foo", **self.BASE, profile_name="minimax"
        )
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "FIRST ACTION" in prompt
        assert "After loading the skill instructions" in prompt

    def test_after_loading_only_with_profile_name(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, profile_name="")
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "After loading" not in prompt

    def test_no_after_loading_for_plain_prompt_with_profile(self):
        spec = build_skill_session_cmd("Fix the bug", **self.BASE, profile_name="minimax")
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "FIRST ACTION" not in prompt
        assert "After loading" not in prompt


def test_skill_cmd_includes_skill_name() -> None:
    spec = build_skill_session_cmd(
        "/autoskillit:planner-analyze some task",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "planner-analyze"


def test_skill_cmd_skill_name_strips_namespace() -> None:
    spec = build_skill_session_cmd(
        "/autoskillit:investigate some issue",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "investigate"


def test_skill_cmd_skill_name_empty_for_non_slash() -> None:
    spec = build_skill_session_cmd(
        "some prompt without slash",
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )
    assert spec.env["AUTOSKILLIT_SKILL_NAME"] == ""


class TestBuildSkillAllowedWritePrefix:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )

    def test_allowed_write_prefix_in_env(self):
        spec = build_skill_session_cmd(
            "/investigate foo", **self.BASE, allowed_write_prefix="/tmp/foo/"
        )
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/foo/"

    def test_allowed_write_prefix_absent_when_empty(self):
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, allowed_write_prefix="")
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in spec.env

    def test_allowed_write_prefix_exclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "old")
        spec = build_skill_session_cmd("/investigate foo", **self.BASE, allowed_write_prefix="new")
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "new"


class TestBuildSkillSessionCmdResume:
    BASE = dict(
        cwd="/repo",
        completion_marker="%%ORDER_UP::abc%%",
        model=None,
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=[],
    )

    def test_resume_flag_present_when_session_id_set(self):
        """--resume <id> is in the command when resume_session_id is set."""
        spec = build_skill_session_cmd(
            "/implement fix the bug", **self.BASE, resume_session_id="sess-12345"
        )
        assert "--resume" in spec.cmd
        idx = spec.cmd.index("--resume")
        assert spec.cmd[idx + 1] == "sess-12345"

    def test_no_resume_flag_when_empty(self):
        """--resume is absent when resume_session_id is empty."""
        spec = build_skill_session_cmd("/implement fix the bug", **self.BASE)
        assert "--resume" not in spec.cmd

    def test_resume_prompt_wraps_with_continuation_context(self):
        """When resuming, the prompt includes continuation instructions."""
        spec = build_skill_session_cmd(
            "/implement fix the bug", **self.BASE, resume_session_id="sess-12345"
        )
        prompt = spec.cmd[spec.cmd.index("-p") + 1]
        assert "resume" in prompt.lower() or "continue" in prompt.lower()
        assert "%%ORDER_UP::abc%%" in prompt

    def test_resume_flag_appended_after_add_dirs(self):
        """--resume flag is appended after all --add-dir entries."""
        from autoskillit.core import ValidatedAddDir

        spec = build_skill_session_cmd(
            "/implement fix the bug",
            **{**self.BASE, "add_dirs": [ValidatedAddDir(path="/extra")]},
            resume_session_id="sess-99",
        )
        # --resume must appear after --add-dir in argv
        assert "--resume" in spec.cmd
        assert "--add-dir" in spec.cmd
        resume_idx = spec.cmd.index("--resume")
        add_dir_idx = spec.cmd.index("--add-dir")
        assert resume_idx > add_dir_idx

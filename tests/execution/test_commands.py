"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ClaudeFlags
from autoskillit.execution.commands import (
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_full_headless_cmd,
    build_headless_cmd,
    build_headless_resume_cmd,
    build_interactive_cmd,
)


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
    def test_resume_session_id_appended(self) -> None:
        result = build_interactive_cmd(resume_session_id="abc123")
        assert "--resume" in result.cmd
        idx = result.cmd.index("--resume")
        assert result.cmd[idx + 1] == "abc123"

    def test_no_resume_flag_when_none(self) -> None:
        result = build_interactive_cmd()
        assert "--resume" not in result.cmd

    def test_resume_placed_before_initial_prompt(self) -> None:
        result = build_interactive_cmd(resume_session_id="abc123", initial_prompt="hello")
        resume_idx = result.cmd.index("--resume")
        prompt_idx = result.cmd.index("hello")
        assert resume_idx < prompt_idx


class TestBuildInteractiveCmdExtended:
    def test_accepts_plugin_dir(self, tmp_path: Path) -> None:
        """build_interactive_cmd with plugin_dir includes --plugin-dir flag."""
        plugin_dir = Path(tmp_path)
        result = build_interactive_cmd(plugin_dir=plugin_dir)
        assert "--plugin-dir" in result.cmd
        idx = result.cmd.index("--plugin-dir")
        assert result.cmd[idx + 1] == str(plugin_dir)

    def test_accepts_add_dirs(self, tmp_path: Path) -> None:
        """build_interactive_cmd with add_dirs includes --add-dir for each entry."""
        d1, d2 = Path(tmp_path) / "a", Path(tmp_path) / "b"
        result = build_interactive_cmd(add_dirs=[d1, d2])
        assert result.cmd.count("--add-dir") == 2

    def test_plugin_dir_none_omits_flag(self) -> None:
        """build_interactive_cmd with plugin_dir=None does not emit --plugin-dir."""
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
            plugin_dir=pkg_root(), add_dirs=[fake_skills_dir]
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

    def test_no_plugin_dir_by_default(self) -> None:
        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert ClaudeFlags.PLUGIN_DIR not in result.cmd

    def test_with_plugin_dir(self) -> None:
        result = build_headless_resume_cmd(
            resume_session_id="abc-123", prompt="Emit token", plugin_dir=Path("/tmp/plugin")
        )
        assert ClaudeFlags.PLUGIN_DIR in result.cmd
        idx = result.cmd.index(ClaudeFlags.PLUGIN_DIR)
        assert result.cmd[idx + 1] == "/tmp/plugin"


class TestBuildFullHeadlessCmd:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_dir="/plugins",
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=2000,
    )

    def test_returns_claude_headless_cmd(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert isinstance(spec, ClaudeHeadlessCmd)

    def test_cmd_starts_with_claude_not_env(self):
        """Argv no longer carries a leading ['env', ...] prefix."""
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert spec.cmd[0] == "claude"
        assert "env" != spec.cmd[0]
        assert not any(tok.startswith("AUTOSKILLIT_HEADLESS=") for tok in spec.cmd)
        assert not any(tok.startswith("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=") for tok in spec.cmd)
        assert not any(tok.startswith("SCENARIO_STEP_NAME=") for tok in spec.cmd)

    def test_env_has_autoskillit_headless(self):
        """AUTOSKILLIT_HEADLESS=1 now lives on spec.env, not in argv."""
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_env_has_exit_delay_when_positive(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    def test_env_omits_exit_delay_when_zero(self):
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_env_strips_sse_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "CLAUDE_CODE_SSE_PORT" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_exit_delay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAUDE_CODE_EXIT_AFTER_STOP_DELAY in host env must be stripped even when ms=0."""
        monkeypatch.setenv("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "99999")
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env

    def test_headless_exclusive_vars_stripped_from_host_env_scenario_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCENARIO_STEP_NAME in host env must be stripped even when no step name is given."""
        monkeypatch.setenv("SCENARIO_STEP_NAME", "outer-step")
        params = {**self.BASE, "scenario_step_name": ""}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert "SCENARIO_STEP_NAME" not in spec.env

    def test_env_has_auto_connect_off(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert spec.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_plugin_dir_present(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--plugin-dir" in spec.cmd
        idx = spec.cmd.index("--plugin-dir")
        assert spec.cmd[idx + 1] == "/plugins"

    def test_output_format_present(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--output-format" in spec.cmd
        idx = spec.cmd.index("--output-format")
        assert spec.cmd[idx + 1] == "stream-json"

    def test_output_format_required_flags_appended(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--verbose" in spec.cmd

    def test_output_format_required_flags_not_duplicated(self):
        """If a required flag is already present it must not be added twice."""
        params = {**self.BASE, "output_format_required_flags": ["--output-format"]}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert spec.cmd.count("--output-format") == 1

    def test_add_dirs_injected(self):
        from autoskillit.core import ValidatedAddDir

        d = ValidatedAddDir(path="/skills/custom")
        params = {**self.BASE, "add_dirs": [d]}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert "--add-dir" in spec.cmd
        idx = spec.cmd.index("--add-dir")
        assert spec.cmd[idx + 1] == "/skills/custom"

    def test_no_add_dirs_emits_no_flag(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_skill_prefix_injected(self):
        """Slash commands must be prefixed with 'Use '."""
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert cmd[prompt_idx].startswith("Use /investigate")

    def test_completion_marker_appended(self):
        """Completion directive must appear in the prompt."""
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "DONE" in cmd[prompt_idx]

    def test_cwd_anchor_appended(self):
        """Working-directory anchor must appear in the prompt."""
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        cmd = spec.cmd
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "/repo" in cmd[prompt_idx]

    def test_model_injected_when_provided(self):
        params = {**self.BASE, "model": "claude-opus-4-6"}
        spec = build_full_headless_cmd("/investigate foo", **params)
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "claude-opus-4-6"

    def test_model_omitted_when_none(self):
        spec = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--model" not in spec.cmd

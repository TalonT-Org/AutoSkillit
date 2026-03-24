"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import ClaudeFlags
from autoskillit.execution.commands import (
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_full_headless_cmd,
    build_headless_cmd,
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

    def test_env_is_empty(self) -> None:
        result = build_interactive_cmd()
        assert result.env == {}

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

    def test_env_is_empty(self) -> None:
        result = build_headless_cmd("some prompt")
        assert result.env == {}

    def test_accepts_model(self) -> None:
        result = build_headless_cmd("some prompt", model="claude-sonnet-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-sonnet-4-6"


class TestBuildFullHeadlessCmd:
    BASE = dict(
        cwd="/repo",
        completion_marker="DONE",
        model=None,
        plugin_dir="/plugins",
        output_format_value="stream-json",
        output_format_required_flags=["--verbose"],
        add_dirs=[],
        exit_after_stop_delay_ms=120000,
    )

    def test_env_prefix_present(self):
        """cmd must start with ['env', 'AUTOSKILLIT_HEADLESS=1', ...]"""
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert cmd[0] == "env"
        assert "AUTOSKILLIT_HEADLESS=1" in cmd

    def test_exit_delay_appended_when_positive(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000" in cmd

    def test_exit_delay_omitted_when_zero(self):
        params = {**self.BASE, "exit_after_stop_delay_ms": 0}
        cmd = build_full_headless_cmd("/investigate foo", **params)
        assert not any("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" in s for s in cmd)

    def test_plugin_dir_present(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--plugin-dir" in cmd
        idx = cmd.index("--plugin-dir")
        assert cmd[idx + 1] == "/plugins"

    def test_output_format_present(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_output_format_required_flags_appended(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--verbose" in cmd

    def test_output_format_required_flags_not_duplicated(self):
        """If a required flag is already present it must not be added twice."""
        params = {**self.BASE, "output_format_required_flags": ["--output-format"]}
        cmd = build_full_headless_cmd("/investigate foo", **params)
        assert cmd.count("--output-format") == 1

    def test_add_dirs_injected(self):
        from autoskillit.core import ValidatedAddDir

        d = ValidatedAddDir(path="/skills/custom")
        params = {**self.BASE, "add_dirs": [d]}
        cmd = build_full_headless_cmd("/investigate foo", **params)
        assert "--add-dir" in cmd
        idx = cmd.index("--add-dir")
        assert cmd[idx + 1] == "/skills/custom"

    def test_no_add_dirs_emits_no_flag(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--add-dir" not in cmd

    def test_skill_prefix_injected(self):
        """Slash commands must be prefixed with 'Use '."""
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert cmd[prompt_idx].startswith("Use /investigate")

    def test_completion_marker_appended(self):
        """Completion directive must appear in the prompt."""
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "DONE" in cmd[prompt_idx]

    def test_cwd_anchor_appended(self):
        """Working-directory anchor must appear in the prompt."""
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        prompt_idx = cmd.index("-p") + 1 if "-p" in cmd else cmd.index("--print") + 1
        assert "/repo" in cmd[prompt_idx]

    def test_model_injected_when_provided(self):
        params = {**self.BASE, "model": "claude-opus-4-6"}
        cmd = build_full_headless_cmd("/investigate foo", **params)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    def test_model_omitted_when_none(self):
        cmd = build_full_headless_cmd("/investigate foo", **self.BASE)
        assert "--model" not in cmd

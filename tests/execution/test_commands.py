"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

import pytest

from autoskillit.core import ClaudeFlags
from autoskillit.execution.commands import (
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
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
    def test_accepts_plugin_dir(self, tmp_path: pytest.TempdirFactory) -> None:
        """build_interactive_cmd with plugin_dir includes --plugin-dir flag."""
        from pathlib import Path

        plugin_dir = Path(tmp_path)
        result = build_interactive_cmd(plugin_dir=plugin_dir)
        assert "--plugin-dir" in result.cmd
        idx = result.cmd.index("--plugin-dir")
        assert result.cmd[idx + 1] == str(plugin_dir)

    def test_accepts_add_dirs(self, tmp_path: pytest.TempdirFactory) -> None:
        """build_interactive_cmd with add_dirs includes --add-dir for each entry."""
        from pathlib import Path

        d1, d2 = Path(tmp_path) / "a", Path(tmp_path) / "b"
        result = build_interactive_cmd(add_dirs=[d1, d2])
        assert result.cmd.count("--add-dir") == 2

    def test_plugin_dir_none_omits_flag(self) -> None:
        """build_interactive_cmd with plugin_dir=None does not emit --plugin-dir."""
        result = build_interactive_cmd()
        assert "--plugin-dir" not in result.cmd

    def test_chefs_hat_uses_builder_output(self, tmp_path: pytest.TempdirFactory) -> None:
        """chefs-hat subprocess cmd is consistent with build_interactive_cmd output."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from autoskillit.core import pkg_root

        fake_skills_dir = Path(tmp_path) / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._chefs_hat as module

            module.chefs_hat()

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

"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

from autoskillit.core import ClaudeFlags
from autoskillit.execution.commands import (
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_headless_cmd,
    build_interactive_cmd,
    build_subrecipe_cmd,
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

    def test_env_has_kitchen_open(self) -> None:
        result = build_interactive_cmd()
        assert result.env.get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

    def test_accepts_model(self) -> None:
        result = build_interactive_cmd(model="claude-opus-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-opus-4-6"

    def test_no_model_flag_when_model_is_none(self) -> None:
        result = build_interactive_cmd(model=None)
        assert ClaudeFlags.MODEL not in result.cmd


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


class TestBuildSubrecipeCmd:
    def test_sets_kitchen_open_env(self) -> None:
        assert build_subrecipe_cmd("p").env.get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

    def test_no_headless_env(self) -> None:
        assert "AUTOSKILLIT_HEADLESS" not in build_subrecipe_cmd("p").env

    def test_has_print_flag(self) -> None:
        assert ClaudeFlags.PRINT in build_subrecipe_cmd("p").cmd

    def test_model_override(self) -> None:
        cmd = build_subrecipe_cmd("p", model="sonnet")
        assert ClaudeFlags.MODEL in cmd.cmd and "sonnet" in cmd.cmd

    def test_returns_headless_cmd_type(self) -> None:
        assert isinstance(build_subrecipe_cmd("p"), ClaudeHeadlessCmd)

    def test_no_model_flag_when_model_is_none(self) -> None:
        assert ClaudeFlags.MODEL not in build_subrecipe_cmd("p", model=None).cmd

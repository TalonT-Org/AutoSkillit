"""Tests for execution/commands.py — ClaudeInteractiveCmd / ClaudeHeadlessCmd builders."""

from __future__ import annotations

import pytest

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

    def test_includes_allow_dangerous_permissions(self) -> None:
        result = build_interactive_cmd()
        assert "--allow-dangerous-permissions" in result.cmd

    def test_does_not_include_dangerously_skip_permissions(self) -> None:
        result = build_interactive_cmd()
        assert "--dangerously-skip-permissions" not in result.cmd

    def test_does_not_include_prompt_flag(self) -> None:
        result = build_interactive_cmd()
        assert "-p" not in result.cmd

    def test_starts_with_claude(self) -> None:
        result = build_interactive_cmd()
        assert result.cmd[0] == "claude"

    def test_env_has_kitchen_open(self) -> None:
        result = build_interactive_cmd()
        assert result.env.get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

    def test_accepts_model(self) -> None:
        result = build_interactive_cmd(model="claude-opus-4-6")
        assert "--model" in result.cmd
        idx = result.cmd.index("--model")
        assert result.cmd[idx + 1] == "claude-opus-4-6"

    def test_no_model_flag_when_model_is_none(self) -> None:
        result = build_interactive_cmd(model=None)
        assert "--model" not in result.cmd


class TestBuildHeadlessCmd:
    def test_returns_correct_type(self) -> None:
        result = build_headless_cmd("some prompt")
        assert isinstance(result, ClaudeHeadlessCmd)

    def test_includes_prompt_flag(self) -> None:
        result = build_headless_cmd("some prompt")
        assert "-p" in result.cmd

    def test_includes_dangerously_skip_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert "--dangerously-skip-permissions" in result.cmd

    def test_does_not_include_allow_dangerous_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert "--allow-dangerous-permissions" not in result.cmd

    def test_env_is_empty(self) -> None:
        result = build_headless_cmd("some prompt")
        assert result.env == {}

    def test_accepts_model(self) -> None:
        result = build_headless_cmd("some prompt", model="claude-sonnet-4-6")
        assert "--model" in result.cmd
        idx = result.cmd.index("--model")
        assert result.cmd[idx + 1] == "claude-sonnet-4-6"

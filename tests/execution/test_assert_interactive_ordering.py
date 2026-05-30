"""Tests for the assert_interactive_ordering runtime gate."""

from __future__ import annotations

import pytest

from autoskillit.core import CmdSpec
from autoskillit.core.types._type_backend import CmdOrigin
from autoskillit.execution.headless._headless_helpers import assert_interactive_ordering

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_correctly_ordered_cmd_passes():
    spec = CmdSpec(
        cmd=("claude", "--dangerously-skip-permissions", "prompt", "--add-dir", "/a"),
        env={},
    )
    assert_interactive_ordering(spec)


def test_positional_after_variadic_raises():
    spec = CmdSpec(
        cmd=("claude", "--dangerously-skip-permissions", "--add-dir", "/a", "prompt"),
        env={},
    )
    with pytest.raises(ValueError, match="positional.*must precede.*variadic"):
        assert_interactive_ordering(spec)


def test_no_positional_passes():
    spec = CmdSpec(
        cmd=("claude", "--dangerously-skip-permissions", "--add-dir", "/a"),
        env={},
    )
    assert_interactive_ordering(spec)


def test_origin_does_not_bypass_validation():
    """Even with a non-None origin, the gate must scan the raw cmd tuple."""
    spec = CmdSpec(
        cmd=("claude", "--add-dir", "/path", "prompt"),
        env={},
        origin=CmdOrigin(
            binary="claude",
            positional=("prompt",),
            variadic_pairs=(("--add-dir", "/path"),),
        ),
    )
    with pytest.raises(ValueError, match="positional.*must precede.*variadic"):
        assert_interactive_ordering(spec)


def test_flag_value_not_mistaken_for_positional():
    """Flag values (e.g., the model name after --model) must not be classified as positional."""
    spec = CmdSpec(
        cmd=("claude", "--model", "claude-sonnet-4-6", "--add-dir", "/a"),
        env={},
    )
    assert_interactive_ordering(spec)


def test_codex_config_override_value_not_mistaken_for_positional():
    """Codex -c flag values must not be classified as positional args."""
    spec = CmdSpec(
        cmd=(
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "hello",
            "-c",
            "developer_instructions=do stuff",
            "--add-dir",
            "/a",
        ),
        env={},
    )
    assert_interactive_ordering(spec)


def test_tools_flag_after_positional_passes():
    spec = CmdSpec(
        cmd=(
            "claude",
            "--dangerously-skip-permissions",
            "my prompt",
            "--tools",
            "AskUserQuestion",
        ),
        env={},
    )
    assert_interactive_ordering(spec)


def test_tools_flag_before_positional_raises():
    spec = CmdSpec(
        cmd=(
            "claude",
            "--dangerously-skip-permissions",
            "--tools",
            "AskUserQuestion",
            "my prompt",
        ),
        env={},
    )
    with pytest.raises(ValueError, match="positional.*must precede.*variadic"):
        assert_interactive_ordering(spec)

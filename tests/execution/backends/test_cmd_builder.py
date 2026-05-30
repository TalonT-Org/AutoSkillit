"""CmdBuilder: ordering invariant and CmdSpec origin tests."""

from __future__ import annotations

import pytest

from autoskillit.core import VARIADIC_CLAUDE_FLAGS
from autoskillit.execution.backends._cmd_builder import CmdBuilder, CmdOrderingError

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_positional_always_precedes_variadic_pairs():
    builder = CmdBuilder("claude")
    builder.mode_flag("--dangerously-skip-permissions")
    builder.positional("my prompt")
    builder.variadic_pair("--add-dir", "/path/a")
    builder.variadic_pair("--tools", "AskUserQuestion")
    spec = builder.build()
    prompt_idx = spec.cmd.index("my prompt")
    for flag in VARIADIC_CLAUDE_FLAGS:
        if flag in spec.cmd:
            assert prompt_idx < spec.cmd.index(flag)


def test_variadic_pair_before_positional_raises():
    builder = CmdBuilder("claude")
    builder.variadic_pair("--add-dir", "/path/a")
    with pytest.raises(CmdOrderingError):
        builder.positional("my prompt")


def test_build_produces_cmdspec_with_origin():
    builder = CmdBuilder("claude")
    builder.positional("hello")
    spec = builder.build()
    assert spec.origin is not None
    assert spec.origin.positional == ("hello",)


def test_mode_flag_appears_before_positional():
    builder = CmdBuilder("claude")
    builder.mode_flag("--dangerously-skip-permissions")
    builder.positional("my prompt")
    spec = builder.build()
    assert spec.cmd.index("--dangerously-skip-permissions") < spec.cmd.index("my prompt")


def test_kv_flag_appears_before_positional():
    builder = CmdBuilder("claude")
    builder.kv_flag("--model", "claude-sonnet-4-6")
    builder.positional("my prompt")
    spec = builder.build()
    assert spec.cmd.index("--model") < spec.cmd.index("my prompt")


def test_build_assembles_correct_order():
    builder = CmdBuilder("claude")
    builder.mode_flag("--dangerously-skip-permissions")
    builder.kv_flag("--model", "claude-sonnet-4-6")
    builder.positional("hello world")
    builder.variadic_pair("--add-dir", "/a")
    builder.variadic_pair("--add-dir", "/b")
    spec = builder.build()
    assert spec.cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in spec.cmd
    assert "--model" in spec.cmd
    assert "hello world" in spec.cmd
    prompt_idx = spec.cmd.index("hello world")
    add_dir_idx = spec.cmd.index("--add-dir")
    assert prompt_idx < add_dir_idx


def test_origin_fields_populated():
    builder = CmdBuilder("claude")
    builder.mode_flag("--mode-flag")
    builder.kv_flag("--model", "m")
    builder.positional("prompt text")
    builder.variadic_pair("--add-dir", "/p")
    spec = builder.build()
    assert spec.origin is not None
    assert spec.origin.binary == "claude"
    assert "--mode-flag" in spec.origin.mode_flags
    assert ("--model", "m") in spec.origin.kv_flags
    assert "prompt text" in spec.origin.positional
    assert ("--add-dir", "/p") in spec.origin.variadic_pairs


def test_no_positional_builds_without_error():
    builder = CmdBuilder("codex")
    builder.variadic_pair("--add-dir", "/a")
    spec = builder.build()
    assert "--add-dir" in spec.cmd
    assert spec.origin is not None
    assert spec.origin.positional == ()

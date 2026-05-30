"""Architectural test: positional initial_prompt must precede all variadic CLI flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import VARIADIC_CLAUDE_FLAGS, ClaudeFlags
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.execution.backends.codex import CodexFlags
from autoskillit.execution.commands import build_interactive_cmd

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.mark.parametrize(
    "variadic_kwargs",
    [
        {"tools": ("AskUserQuestion",)},
        {"add_dirs": [Path("/tmp/a")]},
        {"tools": ("AskUserQuestion",), "add_dirs": [Path("/tmp/a")]},
    ],
)
def test_positional_precedes_all_variadic_flags(variadic_kwargs):
    result = build_interactive_cmd(initial_prompt="test prompt", **variadic_kwargs)
    prompt_idx = result.cmd.index("test prompt")
    for flag in VARIADIC_CLAUDE_FLAGS:
        if flag in result.cmd:
            flag_idx = result.cmd.index(flag)
            assert prompt_idx < flag_idx, (
                f"Positional arg at index {prompt_idx} must precede "
                f"variadic flag {flag!r} at index {flag_idx}"
            )


@pytest.mark.parametrize("backend_cls", [ClaudeCodeBackend, CodexBackend])
@pytest.mark.parametrize(
    "variadic_kwargs",
    [
        {"add_dirs": [Path("/tmp/a")]},
    ],
)
def test_all_backends_positional_precedes_variadic(backend_cls, variadic_kwargs):
    result = backend_cls().build_interactive_cmd(initial_prompt="test prompt", **variadic_kwargs)
    prompt_idx = list(result.cmd).index("test prompt")
    flag_val = CodexFlags.ADD_DIR if backend_cls is CodexBackend else ClaudeFlags.ADD_DIR
    assert flag_val in result.cmd, (
        f"{backend_cls.__name__}: expected variadic flag {flag_val!r} in cmd but not found"
    )
    flag_idx = list(result.cmd).index(flag_val)
    assert prompt_idx < flag_idx, (
        f"{backend_cls.__name__}: positional arg at index {prompt_idx} must precede "
        f"variadic flag {flag_val!r} at index {flag_idx}"
    )

"""Architectural test: positional initial_prompt must precede all variadic CLI flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import VARIADIC_CLAUDE_FLAGS
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

"""Tests for assert_headless_cmd CmdSpec validation."""

from __future__ import annotations

import pytest

from autoskillit.core import CmdSpec

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_assert_headless_cmd_passes_with_p_flag() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=("claude", "-p", "prompt"), env={}))


def test_assert_headless_cmd_raises_without_p_flag() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    with pytest.raises(ValueError, match=r"-p flag"):
        assert_headless_cmd(CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={}))


def test_assert_headless_cmd_non_claude_binary_exempt() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=("codex", "exec", "prompt"), env={}))


def test_assert_headless_cmd_empty_cmd_no_error() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=(), env={}))

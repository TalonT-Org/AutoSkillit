"""T12: backend capability honesty for explorer provisioning models."""

from __future__ import annotations

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_claude_uses_session_scoped_explorer_model() -> None:
    """Claude supports session-scoped exploration, not the terminal per-child model."""
    assert CLAUDE_CODE_CAPABILITIES.session_scoped_explorer_capable is True
    assert CLAUDE_CODE_CAPABILITIES.terminal_explorer_capable is False


def test_session_scoped_and_terminal_are_exclusive_for_claude() -> None:
    """Claude must not claim both models — they are structurally incompatible."""
    assert not (
        CLAUDE_CODE_CAPABILITIES.session_scoped_explorer_capable
        and CLAUDE_CODE_CAPABILITIES.terminal_explorer_capable
    ), "Claude subagents share the parent process — terminal per-child model cannot apply"


def test_codex_uses_terminal_explorer_model() -> None:
    """Codex supports the terminal per-child model, not session-scoped."""
    from autoskillit.execution.backends.codex import CodexBackend

    codex_capabilities = CodexBackend().capabilities
    assert codex_capabilities.terminal_explorer_capable is True
    assert codex_capabilities.session_scoped_explorer_capable is False

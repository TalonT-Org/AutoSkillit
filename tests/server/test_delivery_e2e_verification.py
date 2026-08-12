"""Live end-to-end delivery verification."""

from __future__ import annotations

import pytest

from autoskillit.pipeline import ToolContext
from tests.server._helpers import simulate_session_start

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.anyio]


async def test_claude_inline_delivery_is_one_call(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude backend delivers implementation recipe inline in one call."""
    counter = await simulate_session_start(
        "implementation",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode is not None
    assert counter.delivery_mode == "claude_code_inline", (
        f"Expected claude_code_inline (one-call), got {counter.delivery_mode}"
    )
    assert len(counter) == 1, f"Expected 1 call (one-call inline), got {len(counter)}"


async def test_codex_envelope_delivery_completes(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex backend delivers implementation recipe via bounded ENVELOPE."""
    counter = await simulate_session_start(
        "implementation",
        "codex",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode == "codex_bounded"
    assert len(counter) >= 3  # open_kitchen + at least 1 pull + completion

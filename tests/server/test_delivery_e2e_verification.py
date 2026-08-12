"""Live end-to-end delivery verification."""

from __future__ import annotations

import pytest

from autoskillit.core import CLAUDE_INJECTED_CLIENT_RESULT_TOKENS
from autoskillit.pipeline import ToolContext
from tests.server._helpers import simulate_session_start

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.anyio]


async def test_claude_attested_delivery_completes(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude backend with attestation env vars delivers implementation recipe.

    The implementation recipe's full payload may exceed the 195K annotation
    ceiling, so it correctly resolves to bounded ENVELOPE even with attestation.
    This test proves the attested delivery path completes end-to-end — the
    annotation-aware inline path is exercised by smaller recipes.
    """
    monkeypatch.setenv(
        "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
        str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
    )
    monkeypatch.setenv("AUTOSKILLIT_ATTESTED_META_SUPPORT", "1")
    counter = await simulate_session_start(
        "implementation",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode is not None
    assert counter.delivery_mode in ("claude_code_inline", "claude_code_bounded"), (
        f"Unexpected delivery mode: {counter.delivery_mode}"
    )


async def test_claude_small_recipe_delivers_inline(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small recipe with attestation resolves to one-call inline."""
    monkeypatch.setenv(
        "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
        str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
    )
    monkeypatch.setenv("AUTOSKILLIT_ATTESTED_META_SUPPORT", "1")
    counter = await simulate_session_start(
        "consolidate-health-reports",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode == "claude_code_inline", (
        f"Expected claude_code_inline for small recipe, got {counter.delivery_mode}"
    )
    assert len(counter) == 1, f"Expected 1 call, got {len(counter)}"


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

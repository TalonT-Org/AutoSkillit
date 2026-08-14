"""Live end-to-end delivery verification."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import CLAUDE_INJECTED_CLIENT_RESULT_TOKENS
from autoskillit.pipeline import ToolContext
from autoskillit.server._recipe_delivery import initialize_host_client_attestation
from tests.server._helpers import simulate_session_start

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.anyio]


async def test_claude_attested_implementation_delivers_successfully(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude backend with attestation delivers the implementation recipe.

    The complete canonical artifact remains durable, while public startup is
    the compact initial segment on both ordinary backends.
    """
    monkeypatch.setenv(
        "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
        str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
    )
    monkeypatch.setenv("AUTOSKILLIT_ATTESTED_META_SUPPORT", "1")
    tool_ctx.host_client_attestation = initialize_host_client_attestation()
    counter = await simulate_session_start(
        "implementation",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode == "ordinary_inline"
    assert len(counter) == 1

    # Verify the open_kitchen response has usable content
    result = counter.responses[0]
    envelope = json.loads(result)
    assert envelope.get("success") is True
    assert len(result.encode("utf-8")) < 10_000
    assert envelope["recipe_segment"]["kind"] == "startup"
    assert envelope["recipe_segment"]["bodies"]
    assert "content" not in envelope
    assert "flow_records" not in envelope
    assert "required_sections" not in envelope


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
    tool_ctx.host_client_attestation = initialize_host_client_attestation()
    counter = await simulate_session_start(
        "consolidate-health-reports",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode == "ordinary_inline", (
        f"Expected ordinary_inline for small recipe, got {counter.delivery_mode}"
    )
    assert len(counter) == 1, f"Expected 1 call, got {len(counter)}"


async def test_codex_segmented_delivery_completes(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex backend delivers implementation startup in one compact response."""
    counter = await simulate_session_start(
        "implementation",
        "codex",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    assert counter.delivery_mode == "ordinary_inline"
    assert len(counter) == 1
    envelope = json.loads(counter.responses[0])
    assert envelope["recipe_segment"]["kind"] == "startup"
    assert len(counter.responses[0].encode("utf-8")) < 10_000

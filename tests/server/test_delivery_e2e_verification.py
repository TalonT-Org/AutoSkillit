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

    The implementation recipe's flow records cause significant JSON escaping
    overhead (~288K serialized chars when the ~147K rendered JSON is
    wire-encoded) — exceeding the 175,500-char annotation-aware ceiling.
    The attested path still benefits from the raised unannotated limit
    (46,500 vs 23,250), and the recipe completes delivery end-to-end through
    the bounded ENVELOPE path with usable content.
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
    assert counter.delivery_mode is not None
    assert counter.delivery_mode in ("claude_code_inline", "claude_code_bounded"), (
        f"Unexpected delivery mode: {counter.delivery_mode}"
    )

    # Verify the open_kitchen response has usable content
    result = counter.responses[0]
    envelope = json.loads(result)
    assert envelope.get("success") is True
    if counter.delivery_mode == "claude_code_inline":
        # One-call inline: body and flow in the response
        assert isinstance(envelope.get("content"), str) and len(envelope["content"]) > 0
        assert isinstance(envelope.get("flow_records"), list) and len(envelope["flow_records"]) > 0
        assert "required_sections" not in envelope
    else:
        # Bounded ENVELOPE: manifest with sections to pull
        assert envelope.get("delivery_bound_spill") is True
        assert "required_sections" in envelope


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

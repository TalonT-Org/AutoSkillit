"""Live end-to-end delivery verification."""

from __future__ import annotations

import json
from typing import Any

import pytest

from autoskillit.core import CLAUDE_INJECTED_CLIENT_RESULT_TOKENS
from autoskillit.pipeline import ReadyRecipe, ToolContext
from autoskillit.server._recipe_delivery import initialize_host_client_attestation
from tests.server._helpers import McpCallCounter, simulate_session_start

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.anyio]


async def _record_implementation_bounded_path(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> McpCallCounter:
    from autoskillit.pipeline import ReadyRecipe
    from autoskillit.server._recipe_segment_delivery import (
        attach_recipe_segment,
        prepare_recipe_segment_delivery,
    )
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    counter = await simulate_session_start(
        "implementation",
        "claude-code",
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )
    state = tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    checkpoints = (
        ("clone", "bootstrap_clone"),
        ("claim_and_resolve", "claim_and_resolve_issue"),
        ("create_and_publish", "create_and_publish_branch"),
        ("plan", "complete_run_skill_result"),
    )
    for step_name, tool_name in checkpoints:
        prepared = prepare_recipe_segment_delivery(tool_ctx, step_name)
        assert prepared is not None
        shaped = attach_recipe_segment(
            {"success": True, "checkpoint": step_name},
            prepared,
            success=True,
        )
        raw_response = json.dumps(
            shaped, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        counter.record(
            tool_name,
            state.initialization_id,
            raw_response,
            delivery_shape="SEGMENTED_CHECKPOINT",
            segment_or_section=step_name,
        )
        segment = shaped["recipe_segment"]
        assert isinstance(segment, dict)
        assert segment["kind"] == "success"
        assert segment["source_step"] == step_name
        assert len(raw_response.encode("utf-8")) < 10_000
        pull = segment.get("recipe_pull")
        closures = segment.get("pull_closures", [])
        if not isinstance(pull, dict) or not isinstance(closures, list):
            continue
        identity: dict[str, Any] = {
            key: value for key, value in pull.items() if key != "pull_tool"
        }
        for closure in closures:
            assert isinstance(closure, dict)
            requests = closure.get("pull_requests", [])
            assert isinstance(requests, list)
            for request in requests:
                assert isinstance(request, dict)
                section = request["section"]
                part = request["part"]
                assert isinstance(section, str)
                assert isinstance(part, int)
                raw_page = await get_recipe_section(
                    section=section,
                    part=part,
                    **identity,
                )
                page = json.loads(raw_page)
                assert page["success"] is True
                counter.record(
                    "get_recipe_section",
                    state.initialization_id,
                    raw_page,
                    delivery_shape="SEGMENTED_MANUAL_PULL",
                    segment_or_section=section,
                    part=part,
                )
    return counter


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
    response = counter.responses[0]
    envelope = json.loads(response.raw)
    assert envelope.get("success") is True
    assert response.delivery_shape == "SEGMENTED_INLINE"
    assert response.segment_or_section == "startup"
    assert response.utf8_bytes < 10_000
    assert counter.totals() == {
        "raw_chars": response.raw_chars,
        "utf8_bytes": response.utf8_bytes,
        "client_serialized_chars": response.client_serialized_chars,
        "estimated_tokens": response.estimated_tokens,
        "responses": 1,
    }
    assert envelope["recipe_segment"]["kind"] == "startup"
    assert envelope["recipe_segment"]["bodies"]
    assert "content" not in envelope
    assert "flow_records" not in envelope
    assert "required_sections" not in envelope
    assert isinstance(tool_ctx.recipe_initialization_state, ReadyRecipe)


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
    assert counter.responses[0].delivery_shape == "NON_SEGMENTED_INLINE"
    assert counter.responses[0].segment_or_section == "content"
    assert isinstance(tool_ctx.recipe_initialization_state, ReadyRecipe)


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
    response = counter.responses[0]
    envelope = json.loads(response.raw)
    assert response.delivery_shape == "SEGMENTED_INLINE"
    assert envelope["recipe_segment"]["kind"] == "startup"
    assert response.utf8_bytes < 10_000
    assert isinstance(tool_ctx.recipe_initialization_state, ReadyRecipe)


async def test_implementation_bounded_path_counts_automatic_and_advertised_delivery(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = await _record_implementation_bounded_path(tool_ctx, monkeypatch)

    automatic = [
        record for record in counter.responses if record.delivery_shape == "SEGMENTED_CHECKPOINT"
    ]
    manual = [
        record for record in counter.responses if record.delivery_shape == "SEGMENTED_MANUAL_PULL"
    ]
    assert [record.segment_or_section for record in automatic] == [
        "clone",
        "claim_and_resolve",
        "create_and_publish",
        "plan",
    ]
    assert len(counter.responses) == 1 + len(automatic) + len(manual)
    totals = counter.totals()
    assert totals["responses"] == len(counter.responses)
    assert totals["raw_chars"] == sum(record.raw_chars for record in counter.responses)
    assert totals["utf8_bytes"] == sum(record.utf8_bytes for record in counter.responses)
    assert totals["client_serialized_chars"] == sum(
        record.client_serialized_chars for record in counter.responses
    )
    assert totals["estimated_tokens"] == sum(
        record.estimated_tokens for record in counter.responses
    )
    assert counter.totals(bucket="overhead")["responses"] == 0


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
async def test_non_segmented_envelope_records_every_page_and_completion(
    backend_name: str,
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = await simulate_session_start(
        "research",
        backend_name,
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )

    assert counter.delivery_mode in {"claude_code_bounded", "codex_bounded"}
    assert counter.responses[0].delivery_shape == "NON_SEGMENTED_ENVELOPE"
    page_records = [
        record for record in counter.responses if record.tool_name == "get_recipe_section"
    ]
    completion_records = [
        record
        for record in counter.responses
        if record.tool_name == "complete_recipe_initialization"
    ]
    assert page_records
    assert len(completion_records) == 1
    assert all(record.initialization_id for record in counter.responses)
    assert all(record.segment_or_section for record in page_records)
    assert all(record.part is not None for record in page_records)
    assert counter.totals()["responses"] == len(counter.responses)
    assert counter.totals(bucket="overhead")["responses"] == 0
    assert isinstance(tool_ctx.recipe_initialization_state, ReadyRecipe)
    receipt = json.loads(completion_records[0].raw)
    assert receipt["success"] is True
    assert receipt["initialization_id"] == completion_records[0].initialization_id

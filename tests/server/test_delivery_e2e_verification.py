"""Live end-to-end delivery verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import (
    CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
    RECIPE_RESPONSE_MAX_UTF8_BYTES,
    pkg_root,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline import ReadyRecipe, ToolContext
from autoskillit.server._recipe_delivery import initialize_host_client_attestation
from autoskillit.server._recipe_segment_delivery import RECIPE_SEGMENT_MAX_BYTES
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


@pytest.mark.parametrize("attested", [False, True], ids=["unattested", "attested"])
@pytest.mark.parametrize(
    ("backend_name", "recipe_name", "expected_shape"),
    [
        ("claude-code", "implementation", "SEGMENTED_INLINE"),
        ("claude-code", "consolidate-health-reports", "NON_SEGMENTED_INLINE"),
        ("claude-code", "research", "NON_SEGMENTED_ENVELOPE"),
        ("codex", "implementation", "SEGMENTED_INLINE"),
        ("codex", "consolidate-health-reports", "NON_SEGMENTED_ENVELOPE"),
        ("codex", "research", "NON_SEGMENTED_ENVELOPE"),
    ],
    ids=[
        "claude-implementation",
        "claude-small",
        "claude-research",
        "codex-implementation",
        "codex-small",
        "codex-research",
    ],
)
async def test_delivery_surface_matrix_reaches_ready_with_complete_credit(
    backend_name: str,
    attested: bool,
    recipe_name: str,
    expected_shape: str,
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every valid backend/attestation/recipe-shape combination."""
    if attested:
        monkeypatch.setenv(
            "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
            str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
        )
        monkeypatch.setenv("AUTOSKILLIT_ATTESTED_META_SUPPORT", "1")
        host_attestation = initialize_host_client_attestation()
        assert host_attestation is not None
    else:
        monkeypatch.delenv("AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_ATTESTED_META_SUPPORT", raising=False)
        host_attestation = None
    monkeypatch.setattr(tool_ctx, "host_client_attestation", host_attestation)

    counter = await simulate_session_start(
        recipe_name,
        backend_name,
        tool_ctx=tool_ctx,
        monkeypatch=monkeypatch,
    )

    startup = counter.responses[0]
    payload = json.loads(startup.raw)
    capabilities = BACKEND_REGISTRY[backend_name]().capabilities
    assert startup.delivery_shape == expected_shape
    assert payload["success"] is True
    assert startup.initialization_id

    if expected_shape == "SEGMENTED_INLINE":
        assert len(counter.responses) == 1
        assert startup.utf8_bytes < RECIPE_SEGMENT_MAX_BYTES
        assert payload["recipe_segment"]["kind"] == "startup"
        assert payload["recipe_segment"]["bodies"]
        assert "content" not in payload
        assert "flow_records" not in payload
        assert "required_sections" not in payload
    elif expected_shape == "NON_SEGMENTED_INLINE":
        assert len(counter.responses) == 1
        assert startup.segment_or_section == "content"
        assert startup.client_serialized_chars <= (
            capabilities.unnegotiated_tool_result_token_limit * 4
        )
        assert "content" in payload
    else:
        page_records = [
            record for record in counter.responses if record.tool_name == "get_recipe_section"
        ]
        completion_records = [
            record
            for record in counter.responses
            if record.tool_name == "complete_recipe_initialization"
        ]
        expected_pages = sum(
            requirement["total_parts"] for requirement in payload["required_sections"]
        )
        assert startup.utf8_bytes <= capabilities.unnegotiated_tool_result_token_limit
        assert len(page_records) == expected_pages
        assert len({(record.segment_or_section, record.part) for record in page_records}) == (
            expected_pages
        )
        assert all(record.utf8_bytes <= RECIPE_RESPONSE_MAX_UTF8_BYTES for record in page_records)
        assert len(completion_records) == 1
        receipt = json.loads(completion_records[0].raw)
        assert receipt["success"] is True
        assert receipt["initialization_id"] == startup.initialization_id

    assert counter.totals()["responses"] == len(counter.responses)
    assert all(
        record.initialization_id == startup.initialization_id for record in counter.responses
    )
    assert isinstance(tool_ctx.recipe_initialization_state, ReadyRecipe)


async def test_implementation_bounded_path_counts_automatic_and_advertised_delivery(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    delivery_temp = tmp_path / "bounded-delivery"
    delivery_temp.mkdir()
    monkeypatch.setattr(tool_ctx, "temp_dir", delivery_temp)
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
    # `create_impl_worktree`'s body renders `{{AUTOSKILLIT_SCRIPTS}}`
    # (substitute_scripts_placeholder()) to this checkout's real bundled-scripts
    # path, so its length depends on where the repository happens to be checked
    # out (a long worktree path locally vs. a short fixed CI runner path).
    # Normalize that one occurrence to a fixed-length placeholder before pinning
    # the drift baseline so the assertion is portable across checkouts.
    checkout_scripts_dir = str(pkg_root() / "recipes" / "scripts")
    normalized_counter = McpCallCounter()
    for record in counter.responses:
        normalized_counter.record(
            record.tool_name,
            record.initialization_id,
            record.raw.replace(checkout_scripts_dir, "<CHECKOUT>/src/autoskillit/recipes/scripts"),
            delivery_shape=record.delivery_shape,
            segment_or_section=record.segment_or_section,
            part=record.part,
        )
    totals = normalized_counter.totals()
    # Drift baseline, not a production-safe threshold.
    assert totals == {
        "raw_chars": 22_319,
        "utf8_bytes": 22_333,
        "client_serialized_chars": 24_994,
        "estimated_tokens": 5_581,
        "responses": 5,
    }

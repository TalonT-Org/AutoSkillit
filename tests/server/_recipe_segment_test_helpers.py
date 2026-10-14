"""Shared checkpoint stubs and checks for real handler responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from autoskillit.core import get_tool_def
from autoskillit.pipeline import ReadyRecipe
from autoskillit.server.recipe import _recipe_segment_delivery as delivery_module
from autoskillit.server.recipe._recipe_segment_delivery import (
    RECIPE_SEGMENT_MAX_BYTES,
    PreparedRecipeSegmentDelivery,
    _delivery_tool_name,
    _serialized_bytes,
    _target_segment_indices,
)
from autoskillit.server.recipe._recipe_segment_delivery import (
    prepare_recipe_segment_delivery as _prepare_real_recipe_segment,
)
from autoskillit.server.response._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    enforce_response_budget,
)

_DEFAULT_CARRIER = object()


def install_prepared_recipe_segment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    step_name: str,
    success_carrier: object = _DEFAULT_CARRIER,
    success_pull_carrier: object = _DEFAULT_CARRIER,
) -> PreparedRecipeSegmentDelivery:
    success = (
        {
            "kind": "success",
            "source_step": step_name,
            "bodies": [{"step": "next", "body": "next:\n  action: stop\n"}],
        }
        if success_carrier is _DEFAULT_CARRIER
        else cast(dict[str, Any] | None, success_carrier)
    )
    prepared = PreparedRecipeSegmentDelivery(
        step_name=step_name,
        success_carrier=success,
        success_pull_carrier=(
            success
            if success_pull_carrier is _DEFAULT_CARRIER
            else cast(dict[str, Any] | None, success_pull_carrier)
        ),
        recovery_carrier={
            "kind": "recovery",
            "source_step": step_name,
            "target_steps": ["next"],
            "pull_closure": ["next"],
            "pull_requests": [{"section": "next", "part": 0}],
            "recipe_pull": {
                "pull_tool": "get_recipe_section",
                "payload_sha256": "sha256:" + ("0" * 64),
            },
        },
    )

    def _prepare(_tool_ctx, actual_step_name):
        assert actual_step_name == step_name
        return prepared

    monkeypatch.setattr(delivery_module, "prepare_recipe_segment_delivery", _prepare)
    return prepared


def assert_recovery_recipe_segment(result: dict[str, object], *, step_name: str) -> None:
    segment = result["recipe_segment"]
    assert isinstance(segment, dict)
    assert segment == {
        "kind": "recovery",
        "source_step": step_name,
        "target_steps": ["next"],
        "pull_closure": ["next"],
        "pull_requests": [{"section": "next", "part": 0}],
        "recipe_pull": {
            "pull_tool": "get_recipe_section",
            "payload_sha256": "sha256:" + ("0" * 64),
        },
    }
    assert "bodies" not in segment


def assert_delivery_outcome(tool_name: str, result: dict[str, Any], *, expected: bool) -> None:
    definition = get_tool_def(tool_name)
    assert definition is not None
    outcome = definition.recipe_delivery_outcome
    assert outcome is not None
    assert outcome.succeeded(result) is expected, result


def _real_success_delivery(ready, tool_name: str) -> PreparedRecipeSegmentDelivery:
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    sources = dict.fromkeys(
        source
        for segment in state.finalized_projection.delivery_segments
        for source in segment.checkpoint_sources
    )
    candidates = []
    for source in sources:
        if _delivery_tool_name(state.finalized_projection, source) != tool_name:
            continue
        prepared = _prepare_real_recipe_segment(ready.tool_ctx, source)
        assert prepared is not None
        candidates.append(prepared)
    assert candidates, f"{tool_name} has no mapped checkpoint in the READY recipe"
    return max(
        candidates,
        key=lambda candidate: (
            bool((candidate.success_carrier or {}).get("bodies")),
            bool((candidate.success_carrier or {}).get("pull_closures")),
        ),
    )


async def assert_real_checkpoint_response_budget(
    ready, tool_name: str, result: dict[str, Any], artifact_dir: Path
) -> None:
    assert_delivery_outcome(tool_name, result, expected=True)
    prepared = _real_success_delivery(ready, tool_name)
    assert prepared.success_carrier is not None
    assert prepared.success_pull_carrier is not None
    if not prepared.success_carrier.get("bodies") and not prepared.success_carrier.get(
        "pull_closures"
    ):
        state = ready.tool_ctx.recipe_initialization_state
        assert isinstance(state, ReadyRecipe)
        assert not _target_segment_indices(
            state.finalized_projection, prepared.step_name, success=True
        )
        return
    base = {key: value for key, value in result.items() if key != "recipe_segment"}
    expanded = {**base, "checkpoint_auxiliary": ""}
    inline_size = len(_serialized_bytes({**expanded, "recipe_segment": prepared.success_carrier}))
    assert inline_size < RECIPE_SEGMENT_MAX_BYTES
    expanded["checkpoint_auxiliary"] = "x" * (RECIPE_SEGMENT_MAX_BYTES - inline_size)
    attached = delivery_module.attach_recipe_segment(expanded, prepared, success=True)
    assert attached["recipe_segment"] == prepared.success_pull_carrier
    shaped = enforce_response_budget(
        attached,
        tool_name=tool_name,
        artifact_dir=artifact_dir,
        config=ready.tool_ctx.config.output_budget,
    )
    payload = json.loads(shaped) if isinstance(shaped, str) else shaped
    assert payload.get("subtype") != "recipe_segment_post_effect_delivery_failure"
    assert_delivery_outcome(tool_name, payload, expected=True)
    assert payload["recipe_segment"] == prepared.success_pull_carrier
    assert prepared.success_pull_carrier["source_step"] == prepared.step_name
    assert prepared.success_pull_carrier["recipe_pull"] == prepared.success_carrier["recipe_pull"]
    assert prepared.success_pull_carrier["pull_closures"]
    for key, value in base.items():
        assert payload[key] == value, key
    metadata = payload.get(RESPONSE_SPILL_METADATA_KEY)
    if metadata is not None:
        assert json.loads(Path(metadata["artifact_path"]).read_text()) == expanded

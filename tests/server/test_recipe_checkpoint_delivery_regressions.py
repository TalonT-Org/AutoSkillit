"""Regression coverage for checkpoint carriers across finalized recipe routes."""

from __future__ import annotations

import json
from typing import Any

import pytest

import autoskillit.server.recipe._recipe_segment_delivery as segment_delivery
import autoskillit.server.tools._recipe_section_handler as section_handler
from autoskillit.core import FinalizedRecipeProjection, RecipeFlowEdge, get_tool_def
from autoskillit.core.io import load_yaml
from autoskillit.pipeline import ReadyRecipe
from autoskillit.recipe import result_arms_by_source
from autoskillit.server.recipe._recipe_artifact import load_recipe_artifact
from autoskillit.server.recipe._recipe_segment_delivery import (
    RECIPE_SEGMENT_MAX_BYTES,
    attach_recipe_segment,
    prepare_recipe_segment_delivery,
)
from autoskillit.server.tools.tools_recipe import get_recipe_section
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_READY_PARAMETERS,
    REMEDIATION_READY_PARAMETERS,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_READY_PARAMETERS = [*IMPLEMENTATION_READY_PARAMETERS, *REMEDIATION_READY_PARAMETERS]


def _serialized_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _delivered(carrier: dict[str, Any]) -> set[str]:
    if carrier["kind"] == "recovery":
        return set(carrier["pull_closure"])
    return {body["step"] for body in carrier["bodies"]} | {
        step for closure in carrier["pull_closures"] for step in closure["steps"]
    }


def _carrier_closures(carrier: dict[str, Any]) -> list[tuple[str, ...]]:
    if carrier["kind"] == "recovery":
        return [tuple(carrier["pull_closure"])]
    return [tuple(closure["steps"]) for closure in carrier["pull_closures"]]


def _ready_state(ready: Any) -> ReadyRecipe:
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    assert isinstance(state.finalized_projection, FinalizedRecipeProjection)
    return state


def _automatic_checkpoint_sources(
    projection: FinalizedRecipeProjection,
) -> set[str]:
    automatic_sources: set[str] = set()
    for segment in projection.delivery_segments:
        for source in segment.checkpoint_sources:
            invocation = projection.binding_projection.invocations.get(source)
            if invocation is None:
                continue
            tool_name = (
                "complete_run_skill_result"
                if invocation.tool_name == "run_skill"
                else invocation.tool_name
            )
            definition = get_tool_def(tool_name)
            if definition is not None and definition.automatic_recipe_delivery:
                automatic_sources.add(source)
    return automatic_sources


def _assert_carrier_closures_complete(
    projection: FinalizedRecipeProjection,
    carriers: list[dict[str, Any]],
    *,
    recipe_name: str,
    source: str,
    step_segments: dict[str, Any],
    finalized_steps: frozenset[str],
    automatic_sources: set[str],
) -> None:
    for carrier in carriers:
        assert _serialized_size(carrier) < RECIPE_SEGMENT_MAX_BYTES, (
            f"{recipe_name}:{source}:{carrier['kind']} carrier exceeds bound"
        )
        body_steps = {body["step"] for body in carrier.get("bodies", ())}
        for closure in _carrier_closures(carrier):
            closure_steps = set(closure)
            delivered = body_steps | closure_steps
            assert len(closure_steps) == len(closure)
            assert not closure_steps.intersection({"done", "escalate"})
            assert closure == tuple(
                step for step in projection.ordered_step_names if step in closure_steps
            ), f"{recipe_name}:{source} closure is not declaration ordered"
            for step in delivered:
                assert set(step_segments[step].ordered_step_names) <= delivered, (
                    f"{recipe_name}:{source} closure splits segment at {step}"
                )
            for step in closure_steps - automatic_sources:
                for edge in projection.ordered_flow_edges:
                    if edge.source == step and edge.target in finalized_steps:
                        assert edge.target in delivered, (
                            f"{recipe_name}:{source} closure omits "
                            f"walkable edge {step}->{edge.target}"
                        )


def _assert_success_routes_and_response_bounds(
    projection: FinalizedRecipeProjection,
    prepared: Any,
    *,
    recipe_name: str,
    source: str,
    segment_indices: dict[str, int],
    result_arms_by_source: dict[str, tuple[RecipeFlowEdge, ...]],
) -> None:
    source_index = segment_indices[source]
    for edge in projection.ordered_flow_edges:
        target_index = segment_indices.get(edge.target)
        if (
            edge.source != source
            or edge.edge_type == "skip"
            or target_index is None
            or target_index <= source_index
        ):
            continue
        if not segment_delivery._edge_routes_success(
            projection,
            edge,
            result_arms=result_arms_by_source.get(source, ()),
        ):
            continue
        for carrier in (prepared.success_carrier, prepared.success_pull_only_carrier):
            if carrier is not None:
                assert edge.target in _delivered(carrier), (
                    f"{recipe_name}:{source} success carrier omits {edge.target}"
                )

    for result, success in (
        (
            {"success": True, "result": {"has_changes": True, "commit_count": 1}},
            True,
        ),
        ({"success": False, "error": "checkpoint failed"}, False),
    ):
        attached = attach_recipe_segment(result, prepared, success=success)
        assert _serialized_size(attached) < RECIPE_SEGMENT_MAX_BYTES, (
            f"{recipe_name}:{source} attached response exceeds bound"
        )


@pytest.mark.parametrize("tool_ctx_ready_recipe", _READY_PARAMETERS, indirect=True)
async def test_checkpoint_carriers_deliver_pinned_recipe_routes(tool_ctx_ready_recipe) -> None:
    state = _ready_state(tool_ctx_ready_recipe)
    tool_ctx = tool_ctx_ready_recipe.tool_ctx
    expectations: dict[str, dict[str, set[str]]] = {
        "check_changes": {
            "success_carrier": {"test"},
            "success_pull_only_carrier": {"test"},
            "recovery_carrier": {"release_issue_failure"},
        },
        "ci_watch": {"success_carrier": {"check_repo_merge_state"}},
        "wait_for_queue": {"success_carrier": {"release_issue_success"}},
        "main_repo_guard": {"success_carrier": {"merge", "release_issue_failure"}},
        "commit_guard": {"recovery_carrier": {"merge", "close_issue_already_done"}},
        "check_merge_fix_loop": {"success_carrier": {"release_issue_failure"}},
    }
    if state.recipe_name == "implementation":
        expectations.update(
            {
                "test": {"success_pull_only_carrier": {"commit_guard"}},
                "bind_plan_set": {
                    "success_pull_only_carrier": {
                        "verify",
                        "create_impl_worktree",
                        "check_changes",
                    }
                },
            }
        )

    for source, slots in expectations.items():
        prepared = prepare_recipe_segment_delivery(tool_ctx, source)
        assert prepared is not None, source
        for slot, expected_steps in slots.items():
            carrier = getattr(prepared, slot)
            assert carrier is not None, f"{state.recipe_name}:{source}:{slot}"
            assert expected_steps <= _delivered(carrier), (
                f"{state.recipe_name}:{source}:{slot} omitted "
                f"{expected_steps - _delivered(carrier)}"
            )

    check_changes = prepare_recipe_segment_delivery(tool_ctx, "check_changes")
    assert check_changes is not None and check_changes.success_carrier is not None
    test_body = next(
        body for body in check_changes.success_carrier["bodies"] if body["step"] == "test"
    )
    parsed_test = load_yaml(test_body["body"])
    assert parsed_test["test"]["on_success"] == (
        "commit_guard" if state.recipe_name == "implementation" else "audit_impl"
    )
    attached = attach_recipe_segment(
        {
            "success": True,
            "result": {"has_changes": True, "commit_count": 1},
        },
        check_changes,
        success=True,
    )
    assert attached["recipe_segment"] == check_changes.success_carrier
    assert _serialized_size(attached) < RECIPE_SEGMENT_MAX_BYTES


@pytest.mark.parametrize("tool_ctx_ready_recipe", IMPLEMENTATION_READY_PARAMETERS, indirect=True)
async def test_pull_only_check_changes_section_serves_test_body(
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ready_state(tool_ctx_ready_recipe)
    prepared = prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "check_changes")
    assert prepared is not None and prepared.success_pull_only_carrier is not None
    carrier = prepared.success_pull_only_carrier
    closure = next(closure for closure in carrier["pull_closures"] if "test" in closure["steps"])
    request = next(request for request in closure["pull_requests"] if request["section"] == "test")

    # The READY fixture poisons artifact reads after initialization; this pull must read
    # the production artifact through the production section handler.
    monkeypatch.setattr(section_handler, "load_recipe_artifact", load_recipe_artifact)
    identity = {key: value for key, value in carrier["recipe_pull"].items() if key != "pull_tool"}
    part = request["part"]
    page_plan_sha256 = None
    continuation = None
    content: list[str] = []
    while True:
        page = json.loads(
            await get_recipe_section(
                section=request["section"],
                part=part,
                page_plan_sha256=page_plan_sha256,
                continuation=continuation,
                **identity,
            )
        )
        assert page["success"] is True
        content.append(page["content"])
        if not page["has_more"]:
            break
        part = page["next_part"]
        page_plan_sha256 = page["page_plan_sha256"]
        continuation = page["continuation"]

    body = load_yaml("".join(content))
    assert body["test"]["on_success"] == "commit_guard"
    assert state.recipe_name == "implementation"


@pytest.mark.parametrize("tool_ctx_ready_recipe", _READY_PARAMETERS, indirect=True)
async def test_checkpoint_delivery_closures_and_response_bounds_are_complete(
    tool_ctx_ready_recipe,
) -> None:
    state = _ready_state(tool_ctx_ready_recipe)
    projection = state.finalized_projection
    tool_ctx = tool_ctx_ready_recipe.tool_ctx
    step_segments = {
        step: segment
        for segment in projection.delivery_segments
        for step in segment.ordered_step_names
    }
    segment_indices = {
        step: index
        for index, segment in enumerate(projection.delivery_segments)
        for step in segment.ordered_step_names
    }
    finalized_steps = frozenset(projection.ordered_step_names)
    result_arms = result_arms_by_source(projection.ordered_flow_edges)
    automatic_sources = _automatic_checkpoint_sources(projection)

    for segment in projection.delivery_segments:
        for source in segment.checkpoint_sources:
            if source not in projection.binding_projection.invocations:
                continue
            prepared = prepare_recipe_segment_delivery(tool_ctx, source)
            assert prepared is not None, f"{state.recipe_name}:{source}"
            carriers = [
                carrier
                for carrier in (
                    prepared.success_carrier,
                    prepared.success_pull_only_carrier,
                    prepared.recovery_carrier,
                )
                if carrier is not None
            ]
            _assert_carrier_closures_complete(
                projection,
                carriers,
                recipe_name=state.recipe_name,
                source=source,
                step_segments=step_segments,
                finalized_steps=finalized_steps,
                automatic_sources=automatic_sources,
            )
            _assert_success_routes_and_response_bounds(
                projection,
                prepared,
                recipe_name=state.recipe_name,
                source=source,
                segment_indices=segment_indices,
                result_arms_by_source=result_arms,
            )

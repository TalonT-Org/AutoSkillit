"""Focused tests for opt-in recipe delivery segment finalization."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import (
    FinalizedRecipeProjection,
    FinalizedRecipeStep,
    RecipeBindingProjection,
    RecipeFlowEdge,
    ToolDef,
)
from autoskillit.core.tool_registry import TOOL_REGISTRY
from autoskillit.recipe import result_arms_by_source
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe.io import _parse_recipe, builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeDeliverySegment, RecipeIngredient, RecipeStep
from autoskillit.recipe.validator import (
    _finalize_delivery_segments,
    edge_routes_success,
    validate_recipe_structure,
)
from tests.server._recipe_fixture_parameters import IMPLEMENTATION_OVERRIDES

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe(*, segments: tuple[RecipeDeliverySegment, ...]) -> Recipe:
    return Recipe(
        name="segmented",
        description="segmented recipe",
        kitchen_rules=["follow routes"],
        ingredients={
            "enabled": RecipeIngredient(description="gate", default="false"),
        },
        steps={
            "start": RecipeStep(
                tool="run_python",
                with_args={"step_name": "start"},
                on_success="optional",
            ),
            "optional": RecipeStep(
                tool="run_cmd",
                with_args={"step_name": "optional"},
                skip_when_false="inputs.enabled",
                on_skip="finish",
                on_success="finish",
            ),
            "finish": RecipeStep(
                tool="test_check",
                with_args={"step_name": "finish"},
                on_success="stop",
            ),
            "stop": RecipeStep(action="stop", message="done"),
        },
        delivery_segments=segments,
    )


def test_delivery_segments_absence_is_legacy_and_parse_preserves_order() -> None:
    legacy = _parse_recipe(
        {
            "name": "legacy",
            "description": "legacy",
            "kitchen_rules": ["rule"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    assert legacy.delivery_segments == ()

    parsed = _parse_recipe(
        {
            "name": "segmented",
            "description": "segmented",
            "kitchen_rules": ["rule"],
            "steps": {
                "start": {"action": "route", "on_success": "stop"},
                "stop": {"action": "stop", "message": "done"},
            },
            "delivery_segments": [
                {"name": "Start here", "steps": ["start"]},
                {"name": "Finish", "steps": ["stop"]},
            ],
        }
    )
    assert parsed.delivery_segments == (
        RecipeDeliverySegment(name="Start here", steps=("start",)),
        RecipeDeliverySegment(name="Finish", steps=("stop",)),
    )


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        (
            (RecipeDeliverySegment(name="", steps=("start", "optional", "finish", "stop")),),
            "names must be non-empty",
        ),
        (
            (
                RecipeDeliverySegment(name="same", steps=("start", "optional")),
                RecipeDeliverySegment(name="same", steps=("finish", "stop")),
            ),
            "names must be unique",
        ),
        (
            (
                RecipeDeliverySegment(name="one", steps=("start", "optional")),
                RecipeDeliverySegment(name="two", steps=("finish", "missing")),
            ),
            "unknown steps",
        ),
        (
            (
                RecipeDeliverySegment(name="one", steps=("start", "optional")),
                RecipeDeliverySegment(name="two", steps=("optional", "finish", "stop")),
            ),
            "duplicate steps",
        ),
        (
            (
                RecipeDeliverySegment(name="one", steps=("start", "finish")),
                RecipeDeliverySegment(name="two", steps=("optional", "stop")),
            ),
            "declaration order",
        ),
    ],
)
def test_delivery_segment_declarations_reject_invalid_partitions(
    segments: tuple[RecipeDeliverySegment, ...],
    message: str,
) -> None:
    assert any(message in error for error in validate_recipe_structure(_recipe(segments=segments)))


def test_finalization_uses_only_post_prune_edges_and_removes_empty_segments() -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="removed", steps=("optional",)),
            RecipeDeliverySegment(name="initial", steps=("start",)),
            RecipeDeliverySegment(name="finish", steps=("finish", "stop")),
        )
    )
    pruned, resolutions, _deferred_guard_state = _prune_skipped_steps(recipe)
    assert resolutions == {"optional": False}
    assert tuple(pruned.steps) == ("start", "finish", "stop")
    assert pruned.steps["start"].on_success == "finish"

    edges = (
        RecipeFlowEdge("start", "success", "finish", None, None),
        RecipeFlowEdge("finish", "success", "stop", None, None),
    )
    segments, errors = _finalize_delivery_segments(pruned, edges)
    assert errors == []
    assert tuple(segment.name for segment in segments) == ("initial", "finish")
    assert segments[1].checkpoint_sources == ("start",)

    projection = FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection(invocations={}),
        ingredient_names=frozenset(),
        ordered_step_names=tuple(pruned.steps),
        entrypoint="start",
        ordered_flow_edges=edges,
        ordered_steps=tuple(FinalizedRecipeStep(name=name) for name in pruned.steps),
        delivery_segments=segments,
    )
    assert projection.delivery_segments == segments


def test_finalization_drops_segments_emptied_by_unreachable_sweep(tmp_path) -> None:
    """Segments are finalized from the post-sweep graph, not raw YAML membership."""
    from autoskillit.recipe._api import load_and_validate

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "swept-segments.yaml").write_text(
        """\
name: swept-segments
description: Delivery segments after reachability sweeping.
kitchen_rules:
  - Follow the declared routes.
steps:
  start:
    tool: run_python
    with:
      step_name: start
    on_success: finish
    on_failure: escalate
  finish:
    action: stop
    message: Emit the L3 sentinel block with success=true and reason=Finished.
  stranded:
    action: stop
    message: Emit the L3 sentinel block with success=true and reason=Stranded.
delivery_segments:
  - name: initial
    steps: [start]
  - name: finish
    steps: [finish]
  - name: removed
    steps: [stranded]
"""
    )

    result = load_and_validate(
        "swept-segments",
        project_dir=tmp_path,
        temp_dir=tmp_path / "cache",
        include_finalized_projection=True,
    )

    assert result["valid"], result["errors"]
    projection = result["_finalized_projection"]
    assert "stranded" not in projection.ordered_step_names
    assert tuple(segment.name for segment in projection.delivery_segments) == ("initial", "finish")


def test_checkpoint_tool_must_pass_its_exact_recipe_key() -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="initial", steps=("start", "optional")),
            RecipeDeliverySegment(name="finish", steps=("finish", "stop")),
        )
    )
    recipe.steps["optional"].skip_when_false = None
    recipe.steps["optional"].on_skip = None
    recipe.steps["optional"].tool = "run_python"
    recipe.steps["optional"].with_args["step_name"] = "wrong"
    _segments, errors = _finalize_delivery_segments(
        recipe,
        (RecipeFlowEdge("optional", "success", "finish", None, None),),
    )
    assert errors == [
        "Delivery checkpoint step 'optional' must pass its exact recipe key as with.step_name."
    ]


def test_route_action_crossing_is_served_by_pull_closure() -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="initial", steps=("start",)),
            RecipeDeliverySegment(name="finish", steps=("optional", "finish", "stop")),
        )
    )
    recipe.steps["start"] = RecipeStep(action="route", on_success="optional")

    segments, errors = _finalize_delivery_segments(
        recipe,
        (RecipeFlowEdge("start", "success", "optional", None, None),),
    )

    assert errors == []
    assert segments[1].checkpoint_sources == ("start",)


def test_route_action_requires_exact_finalized_pull_targets() -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="initial", steps=("start",)),
            RecipeDeliverySegment(name="finish", steps=("optional", "finish", "stop")),
        )
    )
    recipe.steps["start"] = RecipeStep(action="route", on_success="optional")

    _segments, errors = _finalize_delivery_segments(recipe, ())

    assert errors == [
        "Route action step 'start' has later-segment targets missing from its finalized pull "
        "closure: ['optional']."
    ]


def test_non_route_action_cannot_cross_delivery_segments() -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="initial", steps=("start",)),
            RecipeDeliverySegment(name="finish", steps=("optional", "finish", "stop")),
        )
    )
    recipe.steps["start"] = RecipeStep(action="confirm", message="Continue?")

    _segments, errors = _finalize_delivery_segments(
        recipe,
        (RecipeFlowEdge("start", "success", "optional", None, None),),
    )

    assert errors == [
        "Cross-segment route from step 'start' requires a tool carrier or an action: route "
        "pull closure."
    ]


@pytest.mark.parametrize(
    ("tool_name", "edge_type", "capability"),
    [
        ("toggle_auto_merge", "success", "automatic_recipe_delivery"),
        ("toggle_auto_merge", "skip", "automatic_recipe_delivery"),
        ("toggle_auto_merge", "failure", "recovery_recipe_delivery"),
    ],
)
def test_checkpoint_tool_requires_matching_delivery_capability(
    tool_name: str,
    edge_type: str,
    capability: str,
) -> None:
    recipe = _recipe(
        segments=(
            RecipeDeliverySegment(name="initial", steps=("start",)),
            RecipeDeliverySegment(name="finish", steps=("optional", "finish", "stop")),
        )
    )
    recipe.steps["start"] = RecipeStep(
        tool=tool_name,
        with_args={"step_name": "start"},
    )

    _segments, errors = _finalize_delivery_segments(
        recipe,
        (RecipeFlowEdge("start", edge_type, "optional", None, None),),
    )

    assert len(errors) == 1
    assert capability in errors[0]


def test_bundled_segmented_recipe_opt_in_is_exact() -> None:
    opted_in = {
        path.stem
        for path in builtin_recipes_dir().glob("*.yaml")
        if load_recipe(path).delivery_segments
    }

    assert opted_in == {"implementation", "remediation"}


# These assertions check broad field/value presence, not production's strict equality
# grammar, so they can independently justify why the registry rejects a result arm.
def _mentions_success_field(condition: str | None, success_field: str) -> bool:
    return (
        condition is not None
        and re.search(rf"\bresult\.{re.escape(success_field)}\b", condition) is not None
    )


def _mentions_success_value(condition: str | None, success_value: bool | str) -> bool:
    if condition is None:
        return False
    flags = re.IGNORECASE if isinstance(success_value, bool) else 0
    return re.search(rf"\b{re.escape(str(success_value))}\b", condition, flags=flags) is not None


def _assert_registry_justifies_rejected_arm(
    tool_name: str,
    definition: ToolDef,
    result_arms: tuple[RecipeFlowEdge, ...],
    arm_index: int,
) -> None:
    edge = result_arms[arm_index]
    if edge_routes_success(
        tool_name,
        edge,
        automatic=definition.automatic_recipe_delivery,
        recovery=definition.recovery_recipe_delivery,
        result_arms=result_arms,
    ):
        return

    outcome = definition.recipe_delivery_outcome
    assert outcome is not None and outcome.recipe_observable
    assert outcome.success_field is not None
    if edge.condition is None:
        assert any(
            _mentions_success_field(earlier.condition, outcome.success_field)
            and _mentions_success_value(earlier.condition, outcome.success_value)
            for earlier in result_arms[:arm_index]
        )
    else:
        assert _mentions_success_field(edge.condition, outcome.success_field)
        assert not _mentions_success_value(edge.condition, outcome.success_value)


@pytest.mark.parametrize(
    ("recipe_name", "defer_unresolved"),
    [
        pytest.param("implementation", False, id="implementation-resolved"),
        pytest.param("implementation", True, id="implementation-deferred"),
        pytest.param("remediation", False, id="remediation-resolved"),
        pytest.param("remediation", True, id="remediation-deferred"),
    ],
)
def test_registry_anchors_automatic_delivery_classification(
    recipe_name: str,
    defer_unresolved: bool,
) -> None:
    overrides = dict(IMPLEMENTATION_OVERRIDES)
    if recipe_name == "remediation":
        overrides["investigate"] = "true"
    result = load_and_validate(
        recipe_name,
        ingredient_overrides=overrides,
        defer_unresolved=defer_unresolved,
        include_finalized_projection=True,
    )
    assert result["valid"], result["errors"]
    projection = result["_finalized_projection"]
    assert projection.delivery_segments
    segment_index = {
        step_name: index
        for index, segment in enumerate(projection.delivery_segments)
        for step_name in segment.ordered_step_names
    }
    check_changes_arms = tuple(
        edge
        for edge in projection.ordered_flow_edges
        if edge.source == "check_changes" and edge.edge_type == "result_condition"
    )
    no_change = next(
        edge
        for edge in check_changes_arms
        if edge.target == "close_issue_no_changes" and edge.condition is not None
    )
    fallback = next(
        edge for edge in check_changes_arms if edge.target == "test" and edge.condition is None
    )
    assert no_change.condition is not None and "result.has_changes" in no_change.condition
    assert segment_index["close_issue_no_changes"] == segment_index["check_changes"]
    assert segment_index["test"] > segment_index["check_changes"]
    assert fallback.condition is None

    for source, result_arms in result_arms_by_source(projection.ordered_flow_edges).items():
        source_step = projection.for_step(source)
        if source_step is None or source_step.tool is None:
            continue
        tool_name = (
            "complete_run_skill_result" if source_step.tool == "run_skill" else source_step.tool
        )
        definition = TOOL_REGISTRY[tool_name]
        if not definition.automatic_recipe_delivery:
            continue
        source_index = segment_index[source]
        for arm_index, edge in enumerate(result_arms):
            if edge.target not in segment_index:
                continue
            if segment_index[edge.target] <= source_index:
                continue
            _assert_registry_justifies_rejected_arm(
                tool_name,
                definition,
                result_arms,
                arm_index,
            )

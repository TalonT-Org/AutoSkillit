"""Focused tests for opt-in recipe delivery segment finalization."""

from __future__ import annotations

import pytest

from autoskillit.core import FinalizedRecipeProjection, RecipeBindingProjection, RecipeFlowEdge
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe.io import _parse_recipe, builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeDeliverySegment, RecipeIngredient, RecipeStep
from autoskillit.recipe.validator import _finalize_delivery_segments, validate_recipe_structure

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
    pruned, resolutions = _prune_skipped_steps(recipe)
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
        ordered_step_names=tuple(pruned.steps),
        entrypoint="start",
        ordered_flow_edges=edges,
        delivery_segments=segments,
    )
    assert projection.delivery_segments == segments


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

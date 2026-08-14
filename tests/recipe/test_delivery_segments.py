"""Focused tests for opt-in recipe delivery segment finalization."""

from __future__ import annotations

import pytest

from autoskillit.core import FinalizedRecipeProjection, RecipeBindingProjection, RecipeFlowEdge
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe.io import _parse_recipe
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
    recipe.steps["optional"].with_args["step_name"] = "wrong"
    _segments, errors = _finalize_delivery_segments(
        recipe,
        (RecipeFlowEdge("optional", "success", "finish", None, None),),
    )
    assert errors == [
        "Delivery checkpoint step 'optional' must pass its exact recipe key as with.step_name."
    ]

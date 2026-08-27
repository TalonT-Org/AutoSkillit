"""Structural validation coverage for server-authoritative step guards."""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe(guard: RecipeStep) -> Recipe:
    return Recipe(
        name="guarded",
        description="guard test",
        steps={
            "guard": guard,
            "next": RecipeStep(action="stop", message="done"),
        },
    )


@pytest.mark.parametrize("value", ["is_silent_type", "${{ context.value }}"])
def test_skip_when_true_requires_a_context_reference(value: str) -> None:
    errors = validate_recipe_structure(_recipe(RecipeStep(tool="run_skill", skip_when_true=value)))
    assert any("skip_when_true must use 'context.<name>' format" in error for error in errors)


def test_skip_when_true_requires_an_on_success_bypass() -> None:
    errors = validate_recipe_structure(
        _recipe(RecipeStep(tool="run_skill", skip_when_true="context.is_silent_type"))
    )
    assert any("missing required on_success bypass target" in error for error in errors)


def test_skip_when_true_rejects_ambiguous_false_guard() -> None:
    errors = validate_recipe_structure(
        _recipe(
            RecipeStep(
                tool="run_skill",
                skip_when_true="context.is_silent_type",
                skip_when_false="inputs.enabled",
                on_skip="next",
                on_success="next",
            )
        )
    )
    assert any("guards are ambiguous" in error for error in errors)


def test_valid_skip_when_true_has_no_guard_errors() -> None:
    errors = validate_recipe_structure(
        _recipe(
            RecipeStep(
                tool="run_skill",
                skip_when_true="context.is_silent_type",
                on_success="next",
            )
        )
    )
    assert not [error for error in errors if "skip_when_true" in error]

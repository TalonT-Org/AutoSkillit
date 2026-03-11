"""Tests for the unknown-sub-recipe semantic rule."""

from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def _recipe_with_run_recipe(name: str) -> Recipe:
    return Recipe(
        name="test",
        description="test",
        kitchen_rules="use run_recipe.",
        steps={
            "call": RecipeStep(
                tool="run_recipe",
                with_args={"name": name, "cwd": "/tmp"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "escalate": RecipeStep(action="stop", message="fail"),
        },
    )


def test_unknown_sub_recipe_fires_for_unknown_name():
    ctx = make_validation_context(
        _recipe_with_run_recipe("no-such"), available_recipes=frozenset({"implementation"})
    )
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "unknown-sub-recipe"]
    assert findings and findings[0].severity == Severity.ERROR
    assert "no-such" in findings[0].message


def test_unknown_sub_recipe_passes_for_known_name():
    ctx = make_validation_context(
        _recipe_with_run_recipe("implementation"),
        available_recipes=frozenset({"implementation"}),
    )
    assert not [f for f in run_semantic_rules(ctx) if f.rule == "unknown-sub-recipe"]


def test_unknown_sub_recipe_passes_for_template_ref():
    ctx = make_validation_context(
        _recipe_with_run_recipe("${{ inputs.recipe_name }}"),
        available_recipes=frozenset({"implementation"}),
    )
    assert not [f for f in run_semantic_rules(ctx) if f.rule == "unknown-sub-recipe"]


def test_unknown_sub_recipe_safe_fallback_with_empty_registry():
    ctx = make_validation_context(
        _recipe_with_run_recipe("any-name"), available_recipes=frozenset()
    )
    assert not [f for f in run_semantic_rules(ctx) if f.rule == "unknown-sub-recipe"]


def test_run_skill_steps_not_checked_by_unknown_sub_recipe():
    recipe = Recipe(
        name="t",
        description="t",
        kitchen_rules="x",
        steps={
            "r": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/foo", "cwd": "/tmp"},
            )
        },
    )
    ctx = make_validation_context(recipe, available_recipes=frozenset({"implementation"}))
    assert not [f for f in run_semantic_rules(ctx) if f.rule == "unknown-sub-recipe"]

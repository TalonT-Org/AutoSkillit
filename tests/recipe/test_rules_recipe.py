"""Tests for rules_recipe semantic rules (unknown-sub-recipe, circular-sub-recipe)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep


def _make_recipe_with_sub_recipe(sub_recipe_name: str) -> Recipe:
    return Recipe(
        name="test-recipe",
        description="Test",
        ingredients={
            "sprint_mode": RecipeIngredient(description="Gate", default="false"),
        },
        steps={
            "sprint_entry": RecipeStep(
                sub_recipe=sub_recipe_name,
                gate="sprint_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )


def test_unknown_sub_recipe_rule_fires() -> None:
    """unknown-sub-recipe finding when sub_recipe name not in available_sub_recipes."""
    recipe = _make_recipe_with_sub_recipe("nonexistent")
    ctx = make_validation_context(
        recipe, available_sub_recipes=frozenset({"sprint-prefix", "other"})
    )
    findings = run_semantic_rules(ctx)
    unknown = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert unknown
    assert "nonexistent" in unknown[0].message


def test_unknown_sub_recipe_rule_passes_when_name_known() -> None:
    """No finding when sub_recipe name is in available_sub_recipes."""
    recipe = _make_recipe_with_sub_recipe("sprint-prefix")
    ctx = make_validation_context(recipe, available_sub_recipes=frozenset({"sprint-prefix"}))
    findings = run_semantic_rules(ctx)
    unknown = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert not unknown


def test_unknown_sub_recipe_rule_skips_non_sub_recipe_steps() -> None:
    """Rule does not fire for steps using tool/action/python/constant discriminators."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={},
        steps={
            "do_work": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi"},
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    ctx = make_validation_context(recipe, available_sub_recipes=frozenset({"sprint-prefix"}))
    findings = run_semantic_rules(ctx)
    unknown = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert not unknown


def test_unknown_sub_recipe_rule_fails_open_when_registry_empty() -> None:
    """No finding when available_sub_recipes is empty (fail-open behavior)."""
    recipe = _make_recipe_with_sub_recipe("any-name")
    ctx = make_validation_context(
        recipe,
        available_sub_recipes=frozenset(),  # empty = fail open
    )
    findings = run_semantic_rules(ctx)
    unknown = [f for f in findings if f.rule == "unknown-sub-recipe"]
    assert not unknown


def test_circular_sub_recipe_rule_fires(tmp_path: Path) -> None:
    """circular-sub-recipe finding when sub_recipe chain forms a cycle."""
    import textwrap

    # Write a self-referencing sub-recipe to the project sub-recipes directory
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    (sub_dir / "loop.yaml").write_text(
        textwrap.dedent("""
            name: loop
            description: Self-referencing sub-recipe
            ingredients:
              gate:
                description: Gate
                default: "true"
            kitchen_rules: []
            steps:
              cycle_step:
                sub_recipe: loop
                gate: gate
                on_success: done
                on_failure: escalate
        """)
    )

    recipe = _make_recipe_with_sub_recipe("loop")
    ctx = make_validation_context(
        recipe,
        available_sub_recipes=frozenset({"loop"}),
        project_dir=tmp_path,
    )
    findings = run_semantic_rules(ctx)
    circular = [f for f in findings if f.rule == "circular-sub-recipe"]
    assert circular, "Expected a circular-sub-recipe finding for self-referencing sub-recipe"
    assert "loop" in circular[0].message


def test_rules_recipe_registered() -> None:
    """Both rules_recipe rules are present in the global registry."""
    rule_names = {spec.name for spec in _RULE_REGISTRY}
    assert "unknown-sub-recipe" in rule_names
    assert "circular-sub-recipe" in rule_names


def test_all_bundled_recipes_pass_rules_recipe() -> None:
    """All bundled recipes (including dev-sprint) pass rules_recipe semantic rules."""
    from autoskillit.recipe.io import builtin_recipes_dir, builtin_sub_recipes_dir, load_recipe

    recipes_dir = builtin_recipes_dir()
    if not recipes_dir.is_dir():
        pytest.skip("No bundled recipes directory")

    sub_recipes_dir = builtin_sub_recipes_dir()
    known_sub_recipes = (
        frozenset(p.stem for p in sub_recipes_dir.glob("*.yaml"))
        if sub_recipes_dir.is_dir()
        else frozenset()
    )

    for recipe_path in sorted(recipes_dir.glob("*.yaml")):
        recipe = load_recipe(recipe_path)
        ctx = make_validation_context(recipe, available_sub_recipes=known_sub_recipes)
        findings = run_semantic_rules(ctx)
        recipe_rules = [
            f for f in findings if f.rule in ("unknown-sub-recipe", "circular-sub-recipe")
        ]
        assert not recipe_rules, (
            f"Recipe '{recipe_path.name}' has rules_recipe findings: "
            f"{[f.message for f in recipe_rules]}"
        )


# ---------------------------------------------------------------------------
# T8 — rules_recipe.py functions use _check_* prefix
# ---------------------------------------------------------------------------


def test_rules_recipe_uses_check_prefix() -> None:
    """Rule functions in rules_recipe.py must use the _check_* naming convention."""
    import autoskillit.recipe.rules_recipe as m

    assert hasattr(m, "_check_unknown_sub_recipe"), "_check_unknown_sub_recipe not found"
    assert hasattr(m, "_check_circular_sub_recipe"), "_check_circular_sub_recipe not found"
    assert not hasattr(m, "_unknown_sub_recipe"), "_unknown_sub_recipe should be renamed"
    assert not hasattr(m, "_circular_sub_recipe"), "_circular_sub_recipe should be renamed"

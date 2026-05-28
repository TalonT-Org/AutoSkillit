"""Tests for _prune_skipped_steps deferral mode and LoadRecipeResult.deferred_guards."""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def _make_recipe_with_skip_guard(step_name: str, ref: str, default: str | None) -> Recipe:
    ingredient_name = ref[len("inputs.") :] if ref.startswith("inputs.") else ref
    return Recipe(
        name="test",
        description="test",
        ingredients={ingredient_name: RecipeIngredient(description="test", default=default)},
        steps={
            step_name: RecipeStep(
                name=step_name, tool="some_tool", skip_when_false=ref, optional=True
            )
        },
    )


class TestPruneSkippedStepsDeferral:
    """Tests for the defer_unresolved=True mode of _prune_skipped_steps."""

    @pytest.mark.small
    def test_prune_skipped_steps_defers_unresolved_guards(self):
        """When a skip_when_false ingredient is absent from overrides, the step must not
        be pruned — it must be marked as deferred (resolution = None)."""
        from autoskillit.recipe._recipe_composition import _prune_skipped_steps

        recipe = _make_recipe_with_skip_guard("review", "inputs.review_approach", default="false")
        pruned, resolutions = _prune_skipped_steps(
            recipe, ingredient_overrides={}, defer_unresolved=True
        )
        assert "review" in pruned.steps, "Step was pruned despite missing override"
        assert resolutions.get("review") is None

    @pytest.mark.small
    def test_prune_skipped_steps_prunes_when_explicitly_false(self):
        """When the ingredient is explicitly 'false' in overrides, the step must be pruned
        (not deferred) even when defer_unresolved=True."""
        from autoskillit.recipe._recipe_composition import _prune_skipped_steps

        recipe = _make_recipe_with_skip_guard("review", "inputs.review_approach", default="false")
        pruned, resolutions = _prune_skipped_steps(
            recipe,
            ingredient_overrides={"review_approach": "false"},
            defer_unresolved=True,
        )
        assert "review" not in pruned.steps
        assert resolutions["review"] is False

    @pytest.mark.medium
    def test_load_and_validate_returns_deferred_guards(self):
        """load_and_validate must expose deferred_guards when skip-guarded ingredients are
        absent from overrides and defer_unresolved=True."""
        from autoskillit.recipe._api import load_and_validate

        result = load_and_validate("remediation", ingredient_overrides={}, defer_unresolved=True)
        deferred = result.get("deferred_guards", [])
        assert "review_approach" in [g["ingredient"] for g in deferred]

    @pytest.mark.medium
    def test_load_and_validate_cache_key_includes_defer_unresolved(self):
        """Calls with identical args but different defer_unresolved must not share cache
        entries — the deferred_guards list must differ between the two results."""
        from autoskillit.recipe._api import load_and_validate

        result_no_defer = load_and_validate(
            "remediation", ingredient_overrides={}, defer_unresolved=False
        )
        result_with_defer = load_and_validate(
            "remediation", ingredient_overrides={}, defer_unresolved=True
        )
        assert result_no_defer.get("deferred_guards", []) == []
        assert len(result_with_defer.get("deferred_guards", [])) > 0

    @pytest.mark.small
    def test_resolve_skip_guards_preserves_deferred_step_block(self):
        """When a resolution is None (deferred), the step block must be preserved in the
        YAML content and only the skip_when_false line removed."""
        from autoskillit.recipe._recipe_composition import _resolve_skip_guards_in_content

        raw = (
            "steps:\n  review:\n    skip_when_false: inputs.review_approach\n    optional: true\n"
        )
        step_obj = RecipeStep(
            name="review",
            tool="some_tool",
            skip_when_false="inputs.review_approach",
            optional=True,
        )
        resolutions: dict[str, bool | None] = {"review": None}
        original_steps = {"review": step_obj}
        result = _resolve_skip_guards_in_content(raw, resolutions, original_steps)
        assert "review:" in result, "Deferred step block was stripped"
        assert "skip_when_false:" not in result, "skip_when_false line not stripped"
        assert "optional: true" in result, "optional: true must be preserved for deferred steps"


@pytest.mark.small
def test_semantic_rule_flags_falsy_default_skip_guard_ingredients():
    """A semantic rule must produce a WARNING for skip_when_false ingredients
    whose default is falsy (or absent). These will cause step deferral in
    interactive sessions."""
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = _make_recipe_with_skip_guard("review", "inputs.review_approach", default="false")
    findings = run_semantic_rules(recipe)
    skip_guard_findings = [f for f in findings if f.rule == "skip-guard-falsy-default"]
    assert len(skip_guard_findings) == 1
    assert skip_guard_findings[0].step_name == "review"


@pytest.mark.medium
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_all_skip_when_false_ingredients_have_deferral_path(recipe_name):
    """Every recipe ingredient referenced in a skip_when_false guard must
    appear in deferred_guards when not provided in overrides."""
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe._recipe_composition import _is_ingredient_truthy

    result = load_and_validate(recipe_name, ingredient_overrides={}, defer_unresolved=True)
    deferred = result.get("deferred_guards", [])
    deferred_ingredients = {g["ingredient"] for g in deferred}

    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe

    info = find_recipe_by_name(recipe_name, pkg_root() / "recipes")
    assert info is not None, f"Bundled recipe '{recipe_name}' not found"
    recipe = load_recipe(info.path)

    for step_name, step in recipe.steps.items():
        if not step.skip_when_false or not step.skip_when_false.startswith("inputs."):
            continue
        ingredient_name = step.skip_when_false[len("inputs.") :]
        ing = recipe.ingredients.get(ingredient_name)
        default = ing.default if ing else None
        if default is None or not _is_ingredient_truthy(str(default)):
            assert ingredient_name in deferred_ingredients, (
                f"Ingredient '{ingredient_name}' (step '{step_name}') in recipe "
                f"'{recipe_name}' has falsy default {default!r} but was not deferred "
                f"when no override was provided."
            )

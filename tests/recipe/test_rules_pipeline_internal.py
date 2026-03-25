"""Tests for the pipeline-internal-not-hidden semantic rule."""

from autoskillit.core import Severity
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeIngredient


def _make_recipe_with_ingredients(ingredients: dict) -> Recipe:
    """Build a minimal Recipe with the given ingredients dict for rule testing."""
    return Recipe(
        name="test",
        description="test",
        ingredients=ingredients,
        steps={},
        kitchen_rules=[],
        version=None,
    )


def test_pipeline_internal_not_hidden_fires_on_set_to_description():
    """Ingredient with 'Set to' description prefix and hidden=False must warn."""
    recipe = _make_recipe_with_ingredients(
        {
            "upfront_claimed": RecipeIngredient(
                description="Set to 'true' when process-issues has already claimed this issue.",
                default="false",
                hidden=False,
            )
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "pipeline-internal-not-hidden"]
    assert len(findings) == 1
    assert findings[0].step_name == "upfront_claimed"
    assert findings[0].severity == Severity.WARNING


def test_pipeline_internal_not_hidden_fires_on_set_by_description():
    """Ingredient with 'Set by' in description and hidden=False must warn."""
    recipe = _make_recipe_with_ingredients(
        {
            "run_mode": RecipeIngredient(
                description="Set by the implementation-groups dispatcher.",
                default="sequential",
                hidden=False,
            )
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "pipeline-internal-not-hidden"]
    assert len(findings) == 1


def test_pipeline_internal_not_hidden_no_fire_when_hidden_true():
    """Pipeline-internal ingredient that already has hidden=True must not warn."""
    recipe = _make_recipe_with_ingredients(
        {
            "sprint_mode": RecipeIngredient(
                description="Set by process-issues to enable sprint mode.",
                default="false",
                hidden=True,
            )
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "pipeline-internal-not-hidden"]
    assert findings == []


def test_pipeline_internal_not_hidden_no_fire_on_user_facing_ingredient():
    """Normal user-facing ingredient with no automation description must not warn."""
    recipe = _make_recipe_with_ingredients(
        {
            "task": RecipeIngredient(
                description="What to implement. Should be a clear problem statement.",
                required=True,
            )
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "pipeline-internal-not-hidden"]
    assert findings == []


def test_pipeline_internal_not_hidden_rule_is_registered():
    """The rule must appear in the canonical rule registry."""
    rule_ids = [r.name for r in _RULE_REGISTRY]
    assert "pipeline-internal-not-hidden" in rule_ids

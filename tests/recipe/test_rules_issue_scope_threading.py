"""Tests for issue-scope-not-threaded-to-walkthrough semantic validation rule.

Verifies that dry-walkthrough steps in recipes with an issue_url (or issue_number)
ingredient receive that ingredient via their with: block. Recipes without issue
ingredients are unaffected.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "issue-scope-not-threaded-to-walkthrough"

_DW_CMD_NO_THREAD = "/autoskillit:dry-walkthrough ${{ context.plan_path }}"


def _make_recipe(
    steps: dict[str, RecipeStep],
    ingredients: dict[str, RecipeIngredient] | None = None,
) -> Recipe:
    return Recipe(
        name="test",
        description="test",
        steps=steps,
        ingredients=ingredients or {},
        kitchen_rules=["test"],
    )


def _issue_url_ingredient() -> dict[str, RecipeIngredient]:
    return {"issue_url": RecipeIngredient(description="GitHub issue URL")}


def _findings(steps: dict[str, RecipeStep], ingredients: dict[str, RecipeIngredient]) -> list:
    recipe = _make_recipe(steps, ingredients)
    return [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]


def _dry_walkthrough_steps(with_args: dict[str, str]) -> dict[str, RecipeStep]:
    return {
        "dry_walkthrough": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _DW_CMD_NO_THREAD, **with_args},
        ),
        "done": RecipeStep(action="stop"),
    }


def test_dry_walkthrough_with_issue_url_threaded_passes():
    """dry-walkthrough receiving issue_url must not fire."""
    findings = _findings(
        _dry_walkthrough_steps({"issue_url": "${{ inputs.issue_url }}"}),
        _issue_url_ingredient(),
    )
    assert findings == []


def test_dry_walkthrough_without_issue_url_fires():
    """dry-walkthrough missing issue_url in a recipe with issue_url ingredient must fire."""
    findings = _findings(_dry_walkthrough_steps({}), _issue_url_ingredient())
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "dry_walkthrough"
    assert "issue_url" in findings[0].message


def test_dry_walkthrough_without_issue_ingredient_passes():
    """Recipes without issue_url or issue_number ingredient must not fire."""
    findings = _findings(_dry_walkthrough_steps({}), {})
    assert findings == []


def test_dry_walkthrough_with_issue_number_threaded_passes():
    """dry-walkthrough receiving issue_number (alternative) must not fire."""
    findings = _findings(
        _dry_walkthrough_steps({"issue_number": "${{ inputs.issue_number }}"}),
        {"issue_number": RecipeIngredient(description="GitHub issue number")},
    )
    assert findings == []


def test_non_dry_walkthrough_step_does_not_fire():
    """Steps invoking other skills must not trigger this rule."""
    steps = {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl ${{ context.plan_path }}"},
        ),
        "done": RecipeStep(action="stop"),
    }
    findings = _findings(steps, _issue_url_ingredient())
    assert findings == []


@pytest.mark.parametrize(
    "recipe_name",
    ["remediation.yaml", "implementation.yaml"],
)
def test_bundled_recipes_pass_issue_scope_threading_rule(recipe_name: str) -> None:
    """Bundled recipes with dry-walkthrough steps must thread issue_url."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]
    assert findings == [], (
        f"{recipe_name} must not trigger {_RULE_NAME}. "
        f"Findings: {[(f.step_name, f.message) for f in findings]}"
    )

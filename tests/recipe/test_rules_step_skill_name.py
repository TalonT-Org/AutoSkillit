"""Tests for step-skill-name-mismatch semantic validation rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RULE_NAME = "step-skill-name-mismatch"

_KNOWN_SKILLS: frozenset[str] = frozenset(
    {
        "review",
        "review-approach",
        "review-pr",
        "make-plan",
        "implement",
        "investigate",
    }
)


def _make_recipe(step_name: str, tool: str = "run_skill", **with_args: str) -> Recipe:
    return Recipe(
        name="test-recipe",
        description="Test recipe for step-skill-name-mismatch rule.",
        version="0.2.0",
        kitchen_rules="test",
        steps={
            step_name: RecipeStep(tool=tool, with_args=with_args),
        },
    )


def _findings(recipe: Recipe):
    ctx = make_validation_context(recipe, available_skills=_KNOWN_SKILLS)
    return [f for f in run_semantic_rules(ctx) if f.rule == RULE_NAME]


def test_no_finding_when_step_key_matches_skill() -> None:
    """Step 'make_plan' normalizes to 'make-plan' which matches the invoked skill."""
    recipe = _make_recipe("make_plan", skill_command="/autoskillit:make-plan task")
    assert _findings(recipe) == []


def test_finding_when_step_key_collides_with_different_skill() -> None:
    """Step 'review' invoking review-approach — 'review' is a different known skill."""
    recipe = _make_recipe("review", skill_command="/autoskillit:review-approach path")
    findings = _findings(recipe)
    assert len(findings) == 1
    assert findings[0].step_name == "review"
    assert findings[0].severity == Severity.WARNING
    assert "review-approach" in findings[0].message


def test_no_finding_for_non_skill_tools() -> None:
    """run_cmd steps are not checked."""
    recipe = _make_recipe("review", tool="run_cmd", cmd="echo ok")
    assert _findings(recipe) == []


def test_no_finding_for_dynamic_skill_name() -> None:
    """Template expressions in skill_command cause resolve_skill_name to return None."""
    recipe = _make_recipe("review", skill_command="/autoskillit:exp-lens-${{ context.slug }}")
    assert _findings(recipe) == []


def test_renamed_step_is_clean() -> None:
    """Step 'review_approach' normalizes to 'review-approach' matching the invoked skill."""
    recipe = _make_recipe("review_approach", skill_command="/autoskillit:review-approach path")
    assert _findings(recipe) == []


def test_no_finding_when_step_key_not_a_known_skill() -> None:
    """Step key 'my_custom_step' doesn't match any known skill — no collision possible."""
    recipe = _make_recipe("my_custom_step", skill_command="/autoskillit:review-approach path")
    assert _findings(recipe) == []

"""Tests for recipe/rules_actions.py semantic validation rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps_dict: dict) -> Recipe:
    steps = {k: RecipeStep(**v) for k, v in steps_dict.items()}
    return Recipe(name="test", description="test", steps=steps)


def test_stop_step_with_routing_fields_fires_error():
    """A stop step with on_success/on_failure should fire stop-step-has-no-routing."""
    recipe = _make_recipe(
        {
            "done": {"action": "stop", "message": "Done.", "on_success": "other"},
            "other": {"action": "stop", "message": "Other."},
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "stop-step-has-no-routing"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR


def test_stop_step_with_default_on_exhausted_passes():
    """A stop step with only the default on_exhausted='escalate' should NOT fire."""
    recipe = _make_recipe({"done": {"action": "stop", "message": "Done."}})
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "stop-step-has-no-routing"]
    assert len(findings) == 0


def test_stop_step_without_routing_passes():
    """A clean stop step should not fire stop-step-has-no-routing."""
    recipe = _make_recipe({"done": {"action": "stop", "message": "Pipeline complete."}})
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "stop-step-has-no-routing"]
    assert len(findings) == 0


def test_recipe_without_terminal_step_fires_error():
    """A recipe with no reachable stop step should fire recipe-has-terminal-step."""
    recipe = _make_recipe(
        {"step_a": {"tool": "run_cmd", "on_success": "step_a", "on_failure": "step_a"}}
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "recipe-has-terminal-step"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR


def test_recipe_with_terminal_step_passes():
    """A recipe with at least one stop step should NOT fire recipe-has-terminal-step."""
    recipe = _make_recipe({"done": {"action": "stop", "message": "Done."}})
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "recipe-has-terminal-step"]
    assert len(findings) == 0


def test_route_step_without_on_result_fires_warning():
    """A route step without on_result conditions should fire route-step-requires-on-result."""
    recipe = _make_recipe(
        {
            "gate": {"action": "route", "on_success": "next"},
            "next": {"action": "stop", "message": "Done."},
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "route-step-requires-on-result"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING


def test_route_step_with_on_result_passes():
    """A route step with on_result should NOT fire route-step-requires-on-result."""
    from autoskillit.recipe.schema import StepResultCondition, StepResultRoute

    # Pydantic accepts a pre-constructed StepResultRoute here even though _make_recipe
    # calls RecipeStep(**v) — Pydantic's model validation accepts existing model instances.
    recipe = _make_recipe(
        {
            "gate": {
                "action": "route",
                "on_result": StepResultRoute(
                    conditions=[StepResultCondition(when="ctx.x == 'y'", route="next")]
                ),
                "on_failure": "next",
            },
            "next": {"action": "stop", "message": "Done."},
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "route-step-requires-on-result"]
    assert len(findings) == 0

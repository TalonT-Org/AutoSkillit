"""Tests for food-truck semantic validation rules (rules_food_truck.py)."""

from __future__ import annotations

import pytest

import autoskillit.recipe  # noqa: F401 -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeKind, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _food_truck_recipe(**kwargs: object) -> Recipe:
    defaults: dict = {
        "name": "my-food-truck",
        "description": "test food truck",
        "kind": RecipeKind.FOOD_TRUCK,
        "steps": {"done": RecipeStep(action="stop", message="sentinel done")},
        "kitchen_rules": ["NEVER"],
    }
    defaults.update(kwargs)
    return Recipe(**defaults)


def _findings(recipe: Recipe, rule: str, **ctx_kwargs: object) -> list:
    ctx = make_validation_context(recipe, **ctx_kwargs)
    return [f for f in run_semantic_rules(ctx) if f.rule == rule]


# ---------------------------------------------------------------------------
# T8: food-truck-has-sentinel-stop (fires when missing)
# ---------------------------------------------------------------------------


def test_food_truck_has_sentinel_stop_rule_fires_on_missing_sentinel():
    """Dispatchable recipe with no sentinel in any stop message triggers a finding."""
    recipe = _food_truck_recipe(
        steps={"done": RecipeStep(action="stop", message="Promotion complete.")},
    )
    found = _findings(recipe, "all-dispatchable-stops-have-sentinel")
    assert found
    assert found[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T9: all-dispatchable-stops-have-sentinel (passes when present)
# ---------------------------------------------------------------------------


def test_food_truck_has_sentinel_stop_rule_passes_when_present():
    """Food-truck recipe with sentinel in stop message produces no findings."""
    recipe = _food_truck_recipe(
        steps={
            "done": RecipeStep(action="stop", message="Done. Emit your L3 sentinel JSON block.")
        },
    )
    found = _findings(recipe, "all-dispatchable-stops-have-sentinel")
    assert found == []


def test_food_truck_sentinel_rule_fires_on_non_sentinel_stop_step():
    """Multi-stop food-truck recipe: one sentinel stop, one plain stop -> fires for plain stop."""
    recipe = _food_truck_recipe(
        steps={
            "done": RecipeStep(
                action="stop",
                message="Emit sentinel JSON block",
            ),
            "escalate_stop": RecipeStep(
                action="stop",
                message="This task cannot proceed.",
            ),
        },
    )
    found = _findings(recipe, "all-dispatchable-stops-have-sentinel")
    assert found, "Rule must fire for stop step without sentinel in message"
    escalate_findings = [f for f in found if f.step_name == "escalate_stop"]
    assert escalate_findings, "Rule must produce a finding for escalate_stop specifically"


def test_sentinel_rule_enforces_on_standard_dispatchable_recipe():
    """Standard-kind dispatchable recipe with plain stop step triggers the sentinel rule."""
    recipe = Recipe(
        name="standard-test",
        description="standard dispatchable recipe",
        steps={
            "done": RecipeStep(action="stop", message="done"),
            "escalate_stop": RecipeStep(
                action="stop",
                message="Escalated.",
            ),
        },
        kitchen_rules=["NEVER"],
    )
    found = _findings(recipe, "all-dispatchable-stops-have-sentinel")
    assert found, "Standard recipe stop steps must have sentinel instructions"
    escalate_findings = [f for f in found if f.step_name == "escalate_stop"]
    assert escalate_findings, "Rule must fire for standard recipe's non-sentinel stop steps"


def test_food_truck_sentinel_rule_passes_when_all_stops_have_sentinel():
    """Multi-stop food-truck recipe where all stop steps mention sentinel -> no findings."""
    recipe = _food_truck_recipe(
        steps={
            "done": RecipeStep(
                action="stop",
                message="Done. Emit your L3 sentinel JSON block.",
            ),
            "escalate_stop": RecipeStep(
                action="stop",
                message="Escalate. Emit L3 sentinel JSON block with success=false.",
            ),
        },
    )
    found = _findings(recipe, "all-dispatchable-stops-have-sentinel")
    assert found == [], "Rule must not fire when all stop steps have sentinel instructions"


def test_food_truck_has_sentinel_stop_severity_is_error():
    """Guard: all-dispatchable-stops-have-sentinel must be ERROR, not WARNING."""
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule = next(
        (r for r in _RULE_REGISTRY if r.name == "all-dispatchable-stops-have-sentinel"), None
    )
    assert rule is not None, "Rule 'all-dispatchable-stops-have-sentinel' not found in registry"
    assert rule.severity == Severity.ERROR


# ---------------------------------------------------------------------------
# T20: escalate-route-coverage
# ---------------------------------------------------------------------------


def test_escalate_route_coverage_rule_fires_when_escalate_routes_present():
    """Escalate routing targets in food-truck steps trigger WARNING findings."""
    recipe = _food_truck_recipe(
        steps={
            "do": RecipeStep(
                action="run_skill",
                on_failure="escalate_stop",
            ),
        },
    )
    found = _findings(recipe, "escalate-route-coverage")
    assert len(found) == 1
    assert found[0].severity == Severity.WARNING
    assert "escalate_stop" in found[0].message


def test_escalate_route_coverage_rule_passes_when_no_escalate_routes():
    """Food-truck recipe without escalate routes produces no findings."""
    recipe = _food_truck_recipe()
    found = _findings(recipe, "escalate-route-coverage")
    assert found == []


def test_escalate_route_coverage_rule_skips_standard():
    """Standard recipe produces no findings for escalate-route-coverage rule."""
    recipe = _standard_recipe()
    found = _findings(recipe, "escalate-route-coverage")
    assert found == []


def test_escalate_route_coverage_severity_is_warning():
    """Guard: escalate-route-coverage must be WARNING, not ERROR."""
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule = next((r for r in _RULE_REGISTRY if r.name == "escalate-route-coverage"), None)
    assert rule is not None, "Rule 'escalate-route-coverage' not found in registry"
    assert rule.severity == Severity.WARNING

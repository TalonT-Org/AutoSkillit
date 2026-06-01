"""Tests for the ingredient-step-name-asymmetry semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _minimal_step(
    skip_when_false: str | None = None,
    on_success: str = "done",
) -> RecipeStep:
    return RecipeStep(
        tool="run_cmd",
        with_args={"cmd": "echo hi"},
        on_success=on_success,
        on_exhausted="escalate",
        skip_when_false=skip_when_false,
        optional=skip_when_false is not None,
    )


def _run_asymmetry_rule(recipe: Recipe) -> list:
    import autoskillit.recipe  # noqa: F401 — triggers @semantic_rule registration
    from autoskillit.recipe.registry import run_semantic_rules

    return [f for f in run_semantic_rules(recipe) if f.rule == "ingredient-step-name-asymmetry"]


def test_asymmetric_1to1_ingredient_fires() -> None:
    recipe = Recipe(
        name="test",
        description="test",
        ingredients={"audit": RecipeIngredient(description="post-merge gate", default="true")},
        steps={
            "audit_impl": _minimal_step("inputs.audit"),
            "done": _minimal_step(),
            "escalate": _minimal_step(),
        },
        kitchen_rules=[],
    )
    findings = _run_asymmetry_rule(recipe)
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    assert findings[0].severity == Severity.WARNING
    assert "audit_impl" in findings[0].message
    assert "audit" in findings[0].message


def test_symmetric_1to1_ingredient_clean() -> None:
    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "investigate": RecipeIngredient(description="run investigation", default="true")
        },
        steps={
            "investigate": _minimal_step("inputs.investigate"),
            "done": _minimal_step(),
            "escalate": _minimal_step(),
        },
        kitchen_rules=[],
    )
    findings = _run_asymmetry_rule(recipe)
    assert findings == [], f"Expected no findings, got: {findings}"


def test_multi_step_ingredient_exempt() -> None:
    many_steps: dict = {f"step_{i}": _minimal_step("inputs.open_pr") for i in range(42)}
    many_steps["done"] = _minimal_step()
    many_steps["escalate"] = _minimal_step()
    recipe = Recipe(
        name="test",
        description="test",
        ingredients={"open_pr": RecipeIngredient(description="PR toggle", default="true")},
        steps=many_steps,
        kitchen_rules=[],
    )
    findings = _run_asymmetry_rule(recipe)
    assert findings == [], f"Expected no findings (multi-step exempt), got: {findings}"


def test_descriptive_flag_exempt() -> None:
    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "test_check_enabled": RecipeIngredient(description="enable tests", default="true")
        },
        steps={
            "test": _minimal_step("inputs.test_check_enabled"),
            "done": _minimal_step(),
            "escalate": _minimal_step(),
        },
        kitchen_rules=[],
    )
    findings = _run_asymmetry_rule(recipe)
    assert findings == [], f"Expected no findings (descriptive flag exempt), got: {findings}"

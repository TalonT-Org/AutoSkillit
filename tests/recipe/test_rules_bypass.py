"""Tests for skip_when_false bypass routing semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

# ---------------------------------------------------------------------------
# skip_when_false bypass routing tests
# ---------------------------------------------------------------------------


def test_optional_without_skip_when_fires_error() -> None:
    """optional: true without skip_when_false must be an ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
                note="Optional step.",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_optional_with_skip_when_does_not_fire() -> None:
    """optional: true WITH skip_when_false must not fire the optional-without-skip-when rule."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.run_audit",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "run_audit": RecipeIngredient(description="", required=False, default="true")
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert rule_findings == []


def test_skip_when_false_referencing_undeclared_ingredient_fires() -> None:
    """skip_when_false must reference a declared ingredient; undeclared must fire ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.nonexistent_ingredient",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        # "nonexistent_ingredient" is NOT in ingredients
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "skip-when-false-undeclared"]
    assert len(rule_findings) == 1

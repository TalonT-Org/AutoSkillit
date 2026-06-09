"""Tests for gitignored-deliverable-in-plan semantic validation rule.

Verifies that recipe steps writing to gitignored directories (e.g.
{{AUTOSKILLIT_TEMP}}/) that feed into an audit-impl step produce a WARNING —
these create unresolvable MISSING findings in the audit→remediation loop.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _plan_step_with_temp_output(name: str) -> RecipeStep:
    """A make-plan-style step that writes to AUTOSKILLIT_TEMP."""
    return RecipeStep(
        tool="run_skill",
        with_args={
            "skill_command": "/autoskillit:make-plan ${{ inputs.issue }}",
            "output_dir": "{{AUTOSKILLIT_TEMP}}/make-plan/",
        },
    )


def _audit_impl_step(name: str = "audit") -> RecipeStep:
    return RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:audit-impl ${{ context.plan_path }}"},
    )


def test_plan_step_with_temp_output_dir_fires() -> None:
    """Rule fires when a plan-writing step feeds into audit-impl downstream."""
    steps = {
        "make_plan": _plan_step_with_temp_output("make_plan"),
        "audit": _audit_impl_step("audit"),
    }
    steps["make_plan"].on_success = "audit"
    steps["audit"].action = "stop"
    steps["audit"].message = "done"
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "gitignored-deliverable-in-plan"]
    assert len(rule_findings) >= 1
    assert rule_findings[0].severity == Severity.WARNING


def test_plan_step_without_audit_impl_downstream_does_not_fire() -> None:
    """Rule does NOT fire when no audit-impl step is downstream of the plan step."""
    steps = {
        "make_plan": _plan_step_with_temp_output("make_plan"),
        "done": RecipeStep(action="stop", message="done"),
    }
    steps["make_plan"].on_success = "done"
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "gitignored-deliverable-in-plan"]
    assert rule_findings == []


def test_non_temp_output_dir_does_not_fire() -> None:
    """Rule does NOT fire when the plan step writes to a non-temp location."""
    plan_step = RecipeStep(
        tool="run_skill",
        with_args={
            "skill_command": "/autoskillit:make-plan ${{ inputs.issue }}",
            "output_dir": "plans/",
        },
    )
    steps = {
        "make_plan": plan_step,
        "audit": _audit_impl_step("audit"),
    }
    steps["make_plan"].on_success = "audit"
    steps["audit"].action = "stop"
    steps["audit"].message = "done"
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "gitignored-deliverable-in-plan"]
    assert rule_findings == []

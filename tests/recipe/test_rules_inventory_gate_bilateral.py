"""Tests for inventory-gate-not-bilateral semantic validation rule.

Verifies that dry-walkthrough steps in recipes with an audit-impl step that
captures remediation_path receive remediation_path via their with: block.
Recipes without audit-impl remediation loops are unaffected.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import CaptureEntrySpec, Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "inventory-gate-not-bilateral"

_AUDIT_IMPL_CMD = "/autoskillit:audit-impl ${{ context.plan_path }}"
_DW_CMD = "/autoskillit:dry-walkthrough ${{ context.plan_path }}"


def _cap(from_: str) -> CaptureEntrySpec:
    return CaptureEntrySpec(from_=from_, value_type="string")


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _audit_impl_with_remediation_capture_steps(
    dw_with_args: dict[str, str] | None = None,
) -> dict[str, RecipeStep]:
    """audit_impl capturing remediation_path, remediation loop routes to verify."""
    dw_with = dw_with_args if dw_with_args is not None else {}
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _AUDIT_IMPL_CMD},
            capture={"remediation_path": _cap("${{ result.remediation_path }}")},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="verify"),
        "verify": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _DW_CMD, **dw_with},
        ),
        "done": RecipeStep(action="stop"),
    }


def _audit_impl_without_remediation_capture_steps() -> dict[str, RecipeStep]:
    """audit_impl without remediation_path capture, remediation loop routes to verify."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _AUDIT_IMPL_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="verify"),
        "verify": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _DW_CMD},
        ),
        "done": RecipeStep(action="stop"),
    }


def _no_audit_impl_steps() -> dict[str, RecipeStep]:
    """Recipe without audit-impl step at all."""
    return {
        "verify": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _DW_CMD},
        ),
        "done": RecipeStep(action="stop"),
    }


def _findings(steps: dict[str, RecipeStep]) -> list:
    recipe = _make_recipe(steps)
    return [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]


def test_inventory_gate_bilateral_fires_when_audit_impl_present() -> None:
    """dry-walkthrough missing remediation_path with audit-impl remediation loop must fire."""
    findings = _findings(_audit_impl_with_remediation_capture_steps())
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "verify"
    assert "remediation_path" in findings[0].message


def test_inventory_gate_bilateral_silent_when_no_audit_impl() -> None:
    """Recipes without audit-impl must not fire."""
    findings = _findings(_no_audit_impl_steps())
    assert findings == []


def test_inventory_gate_bilateral_silent_when_properly_wired() -> None:
    """dry-walkthrough receiving remediation_path must not fire."""
    findings = _findings(
        _audit_impl_with_remediation_capture_steps(
            dw_with_args={"remediation_path": "${{ context.remediation_path }}"}
        )
    )
    assert findings == []


def test_inventory_gate_bilateral_silent_when_audit_impl_no_remediation_capture() -> None:
    """audit_impl without remediation_path capture must not fire."""
    findings = _findings(_audit_impl_without_remediation_capture_steps())
    assert findings == []


@pytest.mark.parametrize(
    "recipe_name",
    [
        "remediation.yaml",
        "implementation.yaml",
        "implementation-groups.yaml",
        "merge-prs.yaml",
    ],
)
def test_bundled_recipes_pass_inventory_gate_bilateral_rule(recipe_name: str) -> None:
    """Bundled recipes must not trigger inventory-gate-not-bilateral."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]
    assert findings == [], (
        f"{recipe_name} must not trigger {_RULE_NAME}. "
        f"Findings: {[(f.step_name, f.message) for f in findings]}"
    )

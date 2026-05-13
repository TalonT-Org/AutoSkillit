"""Tests for audit-impl-remediation-route semantic validation rule.

Verifies that recipe steps invoking audit-impl that capture remediation_path
must have at least one non-GO on_result route targeting a non-terminal step.
Routing all non-GO outcomes to a terminal stop discards the remediation file,
breaking the closed-loop audit-remediate-replan cycle.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _broken_remediation_steps() -> dict[str, RecipeStep]:
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl ${{ inputs.plan }}"},
            capture={"remediation_path": "${{ result.remediation_path }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="run_experiment",
                        when="${{ result.verdict }} == GO",
                    ),
                    StepResultCondition(
                        route="escalate_stop",
                        when=None,
                    ),
                ]
            ),
        ),
        "escalate_stop": RecipeStep(action="stop"),
        "run_experiment": RecipeStep(tool="run_cmd"),
    }


def _correct_remediation_steps() -> dict[str, RecipeStep]:
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl ${{ inputs.plan }}"},
            capture={"remediation_path": "${{ result.remediation_path }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="push",
                        when="${{ result.verdict }} == GO",
                    ),
                    StepResultCondition(
                        route="remediate",
                        when=None,
                    ),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="plan"),
        "plan": RecipeStep(tool="run_cmd"),
        "push": RecipeStep(tool="push_to_remote"),
    }


def _no_capture_steps() -> dict[str, RecipeStep]:
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl ${{ inputs.plan }}"},
            capture={"verdict": "${{ result.verdict }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="escalate_stop",
                        when=None,
                    ),
                ]
            ),
        ),
        "escalate_stop": RecipeStep(action="stop"),
    }


def _non_audit_impl_steps() -> dict[str, RecipeStep]:
    return {
        "some_skill": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:some-other-skill"},
            capture={"remediation_path": "${{ result.remediation_path }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="escalate_stop",
                        when=None,
                    ),
                ]
            ),
        ),
        "escalate_stop": RecipeStep(action="stop"),
    }


def _field_routing_broken_steps() -> dict[str, RecipeStep]:
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl"},
            capture={"remediation_path": "${{ result.remediation_path }}"},
            on_result=StepResultRoute(
                field="verdict",
                routes={
                    "GO": "run_experiment",
                    "NO GO": "escalate_stop",
                },
            ),
        ),
        "escalate_stop": RecipeStep(action="stop"),
        "run_experiment": RecipeStep(tool="run_cmd"),
    }


def _field_routing_correct_steps() -> dict[str, RecipeStep]:
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:audit-impl"},
            capture={"remediation_path": "${{ result.remediation_path }}"},
            on_result=StepResultRoute(
                field="verdict",
                routes={
                    "GO": "push",
                    "NO GO": "remediate",
                },
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="plan"),
        "plan": RecipeStep(tool="run_cmd"),
        "push": RecipeStep(tool="push_to_remote"),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audit_impl_remediation_route_fires_when_no_go_routes_to_terminal() -> None:
    findings = run_semantic_rules(_make_recipe(_broken_remediation_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" in rule_names, (
        "audit-impl-remediation-route must fire when audit-impl captures "
        "remediation_path but routes all non-GO to a terminal stop step."
    )


def test_audit_impl_remediation_route_passes_when_no_go_routes_to_remediate() -> None:
    findings = run_semantic_rules(_make_recipe(_correct_remediation_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" not in rule_names, (
        "audit-impl-remediation-route must not fire when non-GO routes to "
        "a non-terminal remediate step."
    )


def test_audit_impl_remediation_route_passes_when_no_remediation_path_captured() -> None:
    findings = run_semantic_rules(_make_recipe(_no_capture_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" not in rule_names, (
        "audit-impl-remediation-route must not fire when audit-impl does not "
        "capture remediation_path."
    )


def test_audit_impl_remediation_route_ignores_non_audit_impl_steps() -> None:
    findings = run_semantic_rules(_make_recipe(_non_audit_impl_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" not in rule_names, (
        "audit-impl-remediation-route must not fire for non-audit-impl steps "
        "even when they capture something resembling remediation_path."
    )


def test_audit_impl_remediation_route_fires_with_field_based_routing() -> None:
    findings = run_semantic_rules(_make_recipe(_field_routing_broken_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" in rule_names, (
        "audit-impl-remediation-route must fire with legacy field-based on_result "
        "when NO GO routes to terminal."
    )


def test_audit_impl_remediation_route_passes_with_field_based_routing_to_remediate() -> None:
    findings = run_semantic_rules(_make_recipe(_field_routing_correct_steps()))
    rule_names = [f.rule for f in findings]
    assert "audit-impl-remediation-route" not in rule_names, (
        "audit-impl-remediation-route must not fire with legacy field-based on_result "
        "when NO GO routes to a non-terminal step."
    )


def test_audit_impl_remediation_route_severity_is_error() -> None:
    findings = run_semantic_rules(_make_recipe(_broken_remediation_steps()))
    remediation_findings = [f for f in findings if f.rule == "audit-impl-remediation-route"]
    assert len(remediation_findings) >= 1
    for finding in remediation_findings:
        assert finding.severity == Severity.ERROR, (
            f"audit-impl-remediation-route must be ERROR severity, got {finding.severity}"
        )


def test_audit_impl_remediation_route_message_names_step() -> None:
    findings = run_semantic_rules(_make_recipe(_broken_remediation_steps()))
    remediation_findings = [f for f in findings if f.rule == "audit-impl-remediation-route"]
    assert len(remediation_findings) >= 1
    finding = remediation_findings[0]
    assert finding.step_name == "audit_impl", (
        f"Finding must reference step 'audit_impl', got '{finding.step_name}'"
    )
    assert "remediation_path" in finding.message, (
        f"Finding message must mention 'remediation_path', got: {finding.message}"
    )


def test_bundled_recipes_conforming_pass_audit_impl_remediation_route() -> None:
    recipes_dir = builtin_recipes_dir()
    non_conforming = {"research.yaml", "research-implement.yaml"}
    conforming = {
        "implementation.yaml",
        "implementation-groups.yaml",
        "remediation.yaml",
        "merge-prs.yaml",
    }

    for name in non_conforming:
        recipe = load_recipe(recipes_dir / name)
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "audit-impl-remediation-route" in rule_names, (
            f"{name} captures remediation_path but routes all non-GO to terminal "
            f"(pending #2409 fix). Rule must fire."
        )

    for name in conforming:
        recipe = load_recipe(recipes_dir / name)
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "audit-impl-remediation-route" not in rule_names, (
            f"{name} must not trigger audit-impl-remediation-route — it routes "
            f"non-GO to a non-terminal step."
        )

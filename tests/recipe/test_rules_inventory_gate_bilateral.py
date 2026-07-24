"""Tests for executable audit-cycle producer/consumer binding validation."""

from __future__ import annotations

import pytest

from autoskillit.core.types import CaptureEntrySpec, Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "inventory-gate-not-bilateral"


def _cap(from_: str) -> CaptureEntrySpec:
    return CaptureEntrySpec(from_=from_, value_type="string")


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _audit_cycle_steps(
    *,
    capture_authority: bool = True,
    dry_inputs: dict[str, str] | None = None,
) -> dict[str, RecipeStep]:
    audit_capture = (
        {"audit_cycle_path": _cap("${{ result.audit_cycle_path }}")} if capture_authority else {}
    )
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:audit-impl",
                "skill_inputs": {
                    "prior_audit_cycle_path": "/tmp/cycle.json",
                },
            },
            capture=audit_capture,
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="plan", when=None),
                ]
            ),
        ),
        "plan": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:make-plan",
                "skill_inputs": {
                    "task": "task",
                    "audit_cycle_path": "/tmp/cycle.json",
                },
            },
            capture={"plan_disposition_path": _cap("${{ result.plan_disposition_path }}")},
            on_success="verify",
        ),
        "verify": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:dry-walkthrough",
                "skill_inputs": dry_inputs or {"plan_path": "/tmp/plan.md"},
            },
            on_success="audit_impl",
        ),
        "done": RecipeStep(action="stop"),
    }


def _findings(steps: dict[str, RecipeStep]) -> list:
    return [
        finding
        for finding in run_semantic_rules(_make_recipe(steps))
        if finding.rule == _RULE_NAME
    ]


def test_inventory_gate_bilateral_fires_for_missing_dry_tuple() -> None:
    findings = _findings(_audit_cycle_steps())
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "verify"
    assert "audit_cycle_path" in findings[0].message
    assert "plan_disposition_path" in findings[0].message


def test_inventory_gate_bilateral_silent_without_audit_impl() -> None:
    findings = _findings(
        {
            "verify": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:dry-walkthrough plan.md"},
            ),
            "done": RecipeStep(action="stop"),
        }
    )
    assert findings == []


def test_inventory_gate_bilateral_silent_for_compiled_tuple() -> None:
    findings = _findings(
        _audit_cycle_steps(
            dry_inputs={
                "plan_path": "/tmp/plan.md",
                "audit_cycle_path": "/tmp/cycle.json",
                "plan_disposition_path": "/tmp/disposition.json",
            }
        )
    )
    assert findings == []


def test_inventory_gate_bilateral_rejects_missing_authority_capture() -> None:
    findings = _findings(
        _audit_cycle_steps(
            capture_authority=False,
            dry_inputs={
                "plan_path": "/tmp/plan.md",
                "audit_cycle_path": "/tmp/cycle.json",
                "plan_disposition_path": "/tmp/disposition.json",
            },
        )
    )
    assert len(findings) == 1
    assert findings[0].step_name == "audit_impl"
    assert "capture audit_cycle_path" in findings[0].message


def test_inventory_gate_bilateral_rejects_non_dominating_disposition_producer() -> None:
    steps = _audit_cycle_steps(
        dry_inputs={
            "plan_path": "/tmp/plan.md",
            "audit_cycle_path": "/tmp/cycle.json",
            "plan_disposition_path": "/tmp/disposition.json",
        }
    )
    steps["fork"] = RecipeStep(
        tool="run_cmd",
        with_args={"cmd": "echo fork"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="plan", when="result.use_plan"),
                StepResultCondition(route="verify", when=None),
            ]
        ),
    )
    audit_routes = steps["audit_impl"].on_result
    assert audit_routes is not None
    audit_routes.conditions[-1] = StepResultCondition(route="fork", when=None)

    findings = _findings(steps)

    assert any(
        finding.step_name == "verify" and "without crossing" in finding.message
        for finding in findings
    )


def test_inventory_gate_bilateral_rejects_nogo_without_successor_audit() -> None:
    steps = _audit_cycle_steps(
        dry_inputs={
            "plan_path": "/tmp/plan.md",
            "audit_cycle_path": "/tmp/cycle.json",
            "plan_disposition_path": "/tmp/disposition.json",
        }
    )
    steps["verify"].on_success = "done"

    findings = _findings(steps)

    assert any(
        finding.step_name == "audit_impl" and "successor audit-impl" in finding.message
        for finding in findings
    )


@pytest.mark.parametrize(
    "recipe_name",
    [
        "remediation.yaml",
        "implementation.yaml",
        "implementation-groups.yaml",
        "merge-prs.yaml",
        "research-implement.yaml",
        "research.yaml",
    ],
)
def test_bundled_recipes_pass_inventory_gate_bilateral_rule(recipe_name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = [finding for finding in run_semantic_rules(recipe) if finding.rule == _RULE_NAME]
    assert findings == [], (
        f"{recipe_name} must not trigger {_RULE_NAME}. "
        f"Findings: {[(finding.step_name, finding.message) for finding in findings]}"
    )

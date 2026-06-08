"""Tests for audit-impl-plan-scope-mismatch semantic validation rule.

Verifies that audit-impl steps using context.plan_path fire an ERROR when the
recipe contains a remediation loop that re-enters a plan-producing step via
non-GO routes. Recipes using context.all_plan_paths or non-plan-path arguments
(e.g. context.group_files in research recipes) must not fire.
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

_RULE_NAME = "audit-impl-plan-scope-mismatch"

_PLAN_PATH_CMD = "/autoskillit:audit-impl ${{ context.plan_path }} ${{ context.branch_name }} main"
_ALL_PLAN_PATHS_CMD = (
    "/autoskillit:audit-impl ${{ context.all_plan_paths }} ${{ context.branch_name }} main"
)
_GROUP_FILES_CMD = (
    "/autoskillit:audit-impl ${{ context.group_files }} ${{ context.impl_base_sha }} main"
)


def _cap(from_: str) -> CaptureEntrySpec:
    return CaptureEntrySpec(from_=from_, value_type="string")


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _plan_path_remediation_loop_steps() -> dict[str, RecipeStep]:
    """audit_impl with context.plan_path, NO GO routes to remediate -> plan. Must fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _PLAN_PATH_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="plan"),
        "plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan task"},
            capture={"plan_path": _cap("${{ result.plan_path }}")},
        ),
        "done": RecipeStep(action="stop"),
    }


def _all_plan_paths_remediation_loop_steps() -> dict[str, RecipeStep]:
    """audit_impl with context.all_plan_paths, same loop structure. Must not fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _ALL_PLAN_PATHS_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="plan"),
        "plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan task"},
            capture={
                "plan_path": _cap("${{ result.plan_path }}"),
                "all_plan_paths": _cap("${{ result.plan_path }}"),
            },
        ),
        "done": RecipeStep(action="stop"),
    }


def _plan_path_no_remediation_loop_steps() -> dict[str, RecipeStep]:
    """audit_impl with context.plan_path, NO GO routes to a terminal stop. Must not fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _PLAN_PATH_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="escalate", when=None),
                ]
            ),
        ),
        "done": RecipeStep(action="stop"),
        "escalate": RecipeStep(action="stop"),
    }


def _group_files_research_pattern_steps() -> dict[str, RecipeStep]:
    """Research pattern: audit_impl with context.group_files. Must not fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _GROUP_FILES_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="plan"),
        "plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan task"},
            capture={"plan_path": _cap("${{ result.plan_path }}")},
        ),
        "done": RecipeStep(action="stop"),
    }


def _plan_path_remediation_loop_via_make_plan_steps() -> dict[str, RecipeStep]:
    """Same loop via make_plan step (remediation.yaml naming). Must fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _PLAN_PATH_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="commit_guard", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="make_plan"),
        "make_plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan ${{ context.remediation_path }}"},
            capture={"plan_path": _cap("${{ result.plan_path }}")},
        ),
        "commit_guard": RecipeStep(action="stop"),
    }


def _plan_path_remediation_loop_via_rectify_steps() -> dict[str, RecipeStep]:
    """Same loop via rectify step (remediation.yaml initial entry). Must fire."""
    return {
        "rectify": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:rectify ${{ context.investigation_path }}"},
            capture={"plan_path": _cap("${{ result.plan_path }}")},
            on_success="audit_impl",
        ),
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": _PLAN_PATH_CMD},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="make_plan"),
        "make_plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan ${{ context.remediation_path }}"},
            capture={"plan_path": _cap("${{ result.plan_path }}")},
        ),
        "done": RecipeStep(action="stop"),
    }


def _plan_scope_findings(steps: dict[str, RecipeStep]) -> list:
    recipe = _make_recipe(steps)
    return [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]


def test_audit_impl_with_plan_path_in_remediation_loop_fires_error() -> None:
    """plan-path audit_impl re-entering plan step via NO GO must fire ERROR."""
    findings = _plan_scope_findings(_plan_path_remediation_loop_steps())
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "audit_impl"
    assert "context.all_plan_paths" in findings[0].message


def test_audit_impl_with_all_plan_paths_in_remediation_loop_passes() -> None:
    """all_plan_paths audit_impl with same loop structure must not fire."""
    findings = _plan_scope_findings(_all_plan_paths_remediation_loop_steps())
    assert findings == []


def test_audit_impl_without_remediation_loop_passes() -> None:
    """plan-path audit_impl with all non-GO routes going to terminals must not fire."""
    findings = _plan_scope_findings(_plan_path_no_remediation_loop_steps())
    assert findings == []


def test_audit_impl_with_group_files_passes() -> None:
    """Research pattern (context.group_files) must not fire even with remediation loop."""
    findings = _plan_scope_findings(_group_files_research_pattern_steps())
    assert findings == []


def test_audit_impl_remediation_loop_via_make_plan_fires_error() -> None:
    """make_plan is a recognized plan-producing step name and must be detected."""
    findings = _plan_scope_findings(_plan_path_remediation_loop_via_make_plan_steps())
    assert len(findings) == 1
    assert findings[0].step_name == "audit_impl"


def test_audit_impl_remediation_loop_via_rectify_fires_error() -> None:
    """rectify is a recognized plan-producing step name and must be detected."""
    findings = _plan_scope_findings(_plan_path_remediation_loop_via_rectify_steps())
    assert len(findings) == 1
    assert findings[0].step_name == "audit_impl"


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation.yaml",
        "implementation-groups.yaml",
        "remediation.yaml",
        "merge-prs.yaml",
        "research.yaml",
        "research-implement.yaml",
    ],
)
def test_bundled_recipes_have_no_plan_scope_finding(recipe_name: str) -> None:
    """Bundled recipes must fire no audit-impl-plan-scope-mismatch findings."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]
    assert findings == [], (
        f"{recipe_name} must not trigger {_RULE_NAME}. "
        f"Findings: {[(f.step_name, f.message) for f in findings]}"
    )

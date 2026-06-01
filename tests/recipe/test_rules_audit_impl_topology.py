"""Tests for audit-impl-diff-topology-mismatch semantic validation rule.

Verifies that audit-impl steps using SHA-mode against a non-worktree cwd
receive a WARNING when no merge_worktree predecessor exists in the recipe graph.
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

_RULE_NAME = "audit-impl-diff-topology-mismatch"

_SHA_MODE_CMD = "/autoskillit:audit-impl ${{ inputs.plan }} context.base_sha main"
_BRANCH_MODE_CMD = "/autoskillit:audit-impl ${{ inputs.plan }} context.branch_name main"
_SHA_MODE_WORKTREE_CMD = "/autoskillit:audit-impl ${{ inputs.plan }} context.impl_base_sha main"

_NON_WORKTREE_CWD = "${{ context.work_dir }}"
_WORKTREE_CWD = "${{ context.worktree_path }}"


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _sha_pre_merge_steps() -> dict[str, RecipeStep]:
    """SHA-mode, non-worktree cwd, no merge_worktree predecessor — rule should fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": _SHA_MODE_CMD,
                "cwd": _NON_WORKTREE_CWD,
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="audit_impl"),
        "done": RecipeStep(action="stop"),
    }


def _sha_post_merge_steps() -> dict[str, RecipeStep]:
    """SHA-mode, non-worktree cwd, WITH merge_worktree predecessor — rule should not fire."""
    return {
        "merge_step": RecipeStep(
            tool="merge_worktree",
            with_args={},
            on_success="audit_impl",
        ),
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": _SHA_MODE_CMD,
                "cwd": _NON_WORKTREE_CWD,
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="audit_impl"),
        "done": RecipeStep(action="stop"),
    }


def _sha_transitive_merge_steps() -> dict[str, RecipeStep]:
    """SHA-mode, merge_worktree is a transitive (not direct) predecessor — rule should not fire."""
    return {
        "merge_step": RecipeStep(
            tool="merge_worktree",
            with_args={},
            on_success="routing_step",
        ),
        "routing_step": RecipeStep(
            action="route",
            on_success="audit_impl",
        ),
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": _SHA_MODE_CMD,
                "cwd": _NON_WORKTREE_CWD,
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="audit_impl"),
        "done": RecipeStep(action="stop"),
    }


def _branch_mode_steps() -> dict[str, RecipeStep]:
    """Branch-mode (no sha in skill_command) — rule should not fire."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": _BRANCH_MODE_CMD,
                "cwd": _NON_WORKTREE_CWD,
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="audit_impl"),
        "done": RecipeStep(action="stop"),
    }


def _sha_worktree_context_steps() -> dict[str, RecipeStep]:
    """SHA-mode but cwd uses worktree_path — rule should not fire (HEAD = impl tip)."""
    return {
        "audit_impl": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": _SHA_MODE_WORKTREE_CMD,
                "cwd": _WORKTREE_CWD,
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="done", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="remediate", when=None),
                ]
            ),
        ),
        "remediate": RecipeStep(action="route", on_success="audit_impl"),
        "done": RecipeStep(action="stop"),
    }


def _topology_findings(steps: dict[str, RecipeStep]) -> list:
    recipe = _make_recipe(steps)
    return [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]


def test_sha_pre_merge_fires_warning() -> None:
    """SHA-mode audit-impl without merge_worktree predecessor emits a WARNING."""
    findings = _topology_findings(_sha_pre_merge_steps())
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert findings[0].step_name == "audit_impl"
    assert "merge_worktree" in findings[0].message


def test_sha_post_merge_no_finding() -> None:
    """SHA-mode audit-impl with direct merge_worktree predecessor emits no finding."""
    findings = _topology_findings(_sha_post_merge_steps())
    assert findings == []


def test_sha_transitive_merge_no_finding() -> None:
    """SHA-mode audit-impl with transitive merge_worktree predecessor emits no finding."""
    findings = _topology_findings(_sha_transitive_merge_steps())
    assert findings == []


def test_branch_mode_no_finding() -> None:
    """Branch-mode audit-impl emits no finding regardless of merge_worktree presence."""
    findings = _topology_findings(_branch_mode_steps())
    assert findings == []


def test_sha_worktree_context_no_finding() -> None:
    """SHA-mode audit-impl with worktree cwd emits no finding (HEAD = impl tip)."""
    findings = _topology_findings(_sha_worktree_context_steps())
    assert findings == []


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation.yaml",
        "research.yaml",
        "research-implement.yaml",
        "remediation.yaml",
    ],
)
def test_bundled_recipes_have_no_topology_finding(recipe_name: str) -> None:
    """Bundled recipes must fire no audit-impl-diff-topology-mismatch findings."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE_NAME]
    assert findings == [], (
        f"{recipe_name} must not trigger {_RULE_NAME}. Findings: {[f.message for f in findings]}"
    )

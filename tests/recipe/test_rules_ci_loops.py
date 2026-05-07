"""Tests for ci-timed-out-self-loop-unguarded and ci-conflict-path-missing-auto-trigger rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-ci-loops",
        description="Test recipe for CI loop rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


# ---------------------------------------------------------------------------
# ci-timed-out-self-loop-unguarded
# ---------------------------------------------------------------------------


def test_timed_out_self_loop_without_guard_is_error() -> None:
    """wait_for_ci with timed_out→self and no guard step → ERROR."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="ci_watch",
                        when="${{ result.timed_out }} == true",
                    ),
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "ci-timed-out-self-loop-unguarded"]
    assert len(loop_findings) >= 1, (
        "ci-timed-out-self-loop-unguarded must fire for unguarded timed_out self-loop"
    )
    assert loop_findings[0].severity == Severity.ERROR


def test_timed_out_self_loop_with_guard_is_clean() -> None:
    """wait_for_ci with timed_out→self and a check_loop_iteration guard on path → no finding."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="check_loop",
                        when="${{ result.timed_out }} == true",
                    ),
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                ]
            ),
        ),
        "check_loop": RecipeStep(
            tool="run_python",
            with_args={"callable": "autoskillit.smoke_utils.check_loop_iteration"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="ci_watch",
                        when="${{ result.max_exceeded }} == false",
                    ),
                    StepResultCondition(
                        route="done",
                        when="${{ result.max_exceeded }} == true",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "ci-timed-out-self-loop-unguarded"]
    assert len(loop_findings) == 0, (
        "ci-timed-out-self-loop-unguarded must not fire when guard step is on the loop path"
    )


def test_merge_prs_yaml_wait_for_conflict_ci_flags_timed_out_loop() -> None:
    """merge-prs.yaml: wait_for_conflict_ci has guarded timed_out loop — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f
        for f in findings
        if f.rule == "ci-timed-out-self-loop-unguarded" and f.step_name == "wait_for_conflict_ci"
    ]
    assert len(loop_findings) == 0, (
        "wait_for_conflict_ci in merge-prs.yaml must not trigger ci-timed-out-self-loop-unguarded"
    )


def test_merge_prs_yaml_ci_watch_pr_flags_timed_out_loop() -> None:
    """merge-prs.yaml: ci_watch_pr has guarded timed_out loop — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f
        for f in findings
        if f.rule == "ci-timed-out-self-loop-unguarded" and f.step_name == "ci_watch_pr"
    ]
    assert len(loop_findings) == 0, (
        "ci_watch_pr in merge-prs.yaml must not trigger ci-timed-out-self-loop-unguarded"
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation.yaml", "remediation.yaml", "implementation-groups.yaml"],
)
def test_ci_watch_in_recipes_flags_timed_out_loop(recipe_name: str) -> None:
    """Recipes with ci_watch step that has guarded timed_out loop — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f
        for f in findings
        if f.rule == "ci-timed-out-self-loop-unguarded" and f.step_name == "ci_watch"
    ]
    assert len(loop_findings) == 0, (
        f"{recipe_name}: ci_watch must not trigger ci-timed-out-self-loop-unguarded"
    )


# ---------------------------------------------------------------------------
# ci-conflict-path-missing-auto-trigger
# ---------------------------------------------------------------------------


def test_conflict_path_without_auto_trigger_is_error() -> None:
    """wait_for_ci on conflict path without auto_trigger → ERROR."""
    steps = {
        "check_mergeability": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:check-pr-mergeable pr main"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="wait_for_conflict_ci",
                        when="${{ result.mergeable_status }} == CONFLICTING",
                    ),
                    StepResultCondition(
                        route="ci_watch",
                        when="${{ result.mergeable_status }} == MERGEABLE",
                    ),
                ]
            ),
        ),
        "wait_for_conflict_ci": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                    StepResultCondition(
                        route="done",
                        when="${{ result.timed_out }} == true",
                    ),
                ]
            ),
        ),
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300, "auto_trigger": "true"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    trigger_findings = [f for f in findings if f.rule == "ci-conflict-path-missing-auto-trigger"]
    assert len(trigger_findings) >= 1, (
        "ci-conflict-path-missing-auto-trigger must fire for conflict path without auto_trigger"
    )
    assert trigger_findings[0].severity == Severity.ERROR


def test_conflict_path_with_auto_trigger_is_clean() -> None:
    """wait_for_ci on conflict path with auto_trigger: true → no finding."""
    steps = {
        "check_mergeability": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:check-pr-mergeable pr main"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="wait_for_conflict_ci",
                        when="${{ result.mergeable_status }} == CONFLICTING",
                    ),
                ]
            ),
        ),
        "wait_for_conflict_ci": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300, "auto_trigger": "true"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    trigger_findings = [f for f in findings if f.rule == "ci-conflict-path-missing-auto-trigger"]
    assert len(trigger_findings) == 0, (
        "ci-conflict-path-missing-auto-trigger must not fire when auto_trigger is set"
    )


def test_merge_prs_yaml_wait_for_conflict_ci_flags_missing_auto_trigger() -> None:
    """merge-prs.yaml: wait_for_conflict_ci has auto_trigger: true — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    trigger_findings = [
        f
        for f in findings
        if f.rule == "ci-conflict-path-missing-auto-trigger"
        and f.step_name == "wait_for_conflict_ci"
    ]
    assert len(trigger_findings) == 0, (
        "wait_for_conflict_ci in merge-prs.yaml must not trigger ci-conflict-path-missing-auto-trigger"
    )


def test_merge_prs_yaml_ci_watch_pr_flags_missing_auto_trigger() -> None:
    """merge-prs.yaml: ci_watch_pr has auto_trigger: true — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    trigger_findings = [
        f
        for f in findings
        if f.rule == "ci-conflict-path-missing-auto-trigger" and f.step_name == "ci_watch_pr"
    ]
    assert len(trigger_findings) == 0, (
        "ci_watch_pr in merge-prs.yaml must not trigger ci-conflict-path-missing-auto-trigger"
    )


def test_non_conflict_path_ci_watch_is_not_flagged() -> None:
    """wait_for_ci not on a conflict path must not trigger the rule."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.success }} == true",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    trigger_findings = [f for f in findings if f.rule == "ci-conflict-path-missing-auto-trigger"]
    assert len(trigger_findings) == 0, (
        "Non-conflict-path wait_for_ci must not trigger ci-conflict-path-missing-auto-trigger"
    )

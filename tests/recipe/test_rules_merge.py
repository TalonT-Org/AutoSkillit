"""Tests for recipe/rules_merge.py semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules_merge import _RECOVERABLE_FAILED_STEPS
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for rules_merge tests."""
    return Recipe(
        name="test-rules-merge",
        description="Test recipe for merge routing rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _conditions_for(*step_values: str) -> list[StepResultCondition]:
    """Build on_result conditions for the given failed_step values."""
    return [
        StepResultCondition(
            route="recover",
            when=f"result.failed_step == '{v}'",
        )
        for v in step_values
    ]


def test_no_merge_worktree_step_is_clean() -> None:
    """Recipe without merge_worktree → no findings."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_no_on_result_is_clean() -> None:
    """merge_worktree present but no on_result → no finding."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_all_recoverable_steps_routed_is_clean() -> None:
    """on_result conditions cover all four recoverable steps → no finding."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*all_values)),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_missing_one_recoverable_step_is_error() -> None:
    """Covers 3 of 4 recoverable steps → ERROR, message lists the missing one."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)
    present = all_values[:-1]  # all but the last
    missing = all_values[-1]

    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*present)),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    assert missing in flagged[0].message


def test_merge_worktree_missing_all_recoverable_steps_is_error() -> None:
    """has on_result but no step matches _RECOVERABLE_FAILED_STEPS → ERROR, all 4 in message."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="done", when="result.failed_step == 'other'")
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    for step_value in _RECOVERABLE_FAILED_STEPS:
        assert step_value in flagged[0].message


def test_merge_without_commit_guard_fires() -> None:
    """test_check → merge_worktree with no commit_guard predecessor → ERROR."""
    recipe = _make_recipe(
        {
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="merge",
                on_failure="abort",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "abort": RecipeStep(action="stop", message="abort"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert len(flagged) == 1, (
        f"Expected exactly 1 merge-without-commit-guard ERROR, got: {flagged}"
    )
    assert flagged[0].severity == Severity.ERROR
    assert flagged[0].step_name == "merge"


def test_merge_with_commit_guard_step_name_is_clean() -> None:
    """Step named commit_guard* immediately before merge_worktree → no finding."""
    recipe = _make_recipe(
        {
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="commit_guard",
                on_failure="abort",
            ),
            "commit_guard": RecipeStep(
                tool="run_cmd",
                with_args={
                    "cmd": (
                        "cd /tmp/wt && "
                        'if [ -n "$(git status --porcelain)" ]; '
                        "then git add -A && git commit -m 'chore: pending'; fi"
                    ),
                    "cwd": "/tmp/wt",
                },
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "abort": RecipeStep(action="stop", message="abort"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert flagged == [], f"Unexpected merge-without-commit-guard finding: {flagged}"


def test_merge_with_git_commit_in_cmd_is_clean() -> None:
    """run_cmd step with 'git commit' in cmd immediately before merge → no finding."""
    recipe = _make_recipe(
        {
            "pre_merge": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "git add -A && git commit -m 'fix' || true"},
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert flagged == [], f"Unexpected merge-without-commit-guard finding: {flagged}"


def test_multiple_merge_steps_each_checked_independently() -> None:
    """Two merge_worktree steps: one complete, one incomplete → one ERROR."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)

    recipe = _make_recipe(
        {
            "merge_ok": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt1", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*all_values)),
                on_success="done",
                on_failure="done",
            ),
            "merge_bad": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt2", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="done", when=f"result.failed_step == '{all_values[0]}'"
                        )
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].step_name == "merge_bad"

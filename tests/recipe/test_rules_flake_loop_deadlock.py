"""Tests for rules_flake_loop.py: flake-suspected-unwinnable-loop rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-flake-loop-deadlock",
        description="Test recipe for flake-suspected-unwinnable-loop rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _merge_on_result() -> StepResultRoute:
    return StepResultRoute(
        conditions=[
            StepResultCondition(when="result.failed_step == 'dirty_tree'", route="check_loop"),
            StepResultCondition(when="result.failed_step == 'test_gate'", route="check_loop"),
            StepResultCondition(
                when="result.failed_step == 'post_rebase_test_gate'", route="check_loop"
            ),
            StepResultCondition(when="result.failed_step == 'rebase'", route="escalate"),
            StepResultCondition(when="result.failed_step == 'dirty_main_repo'", route="escalate"),
            StepResultCondition(when="result.error", route="escalate"),
            StepResultCondition(when=None, route="done"),
        ]
    )


def test_flake_suspected_to_merge_without_context_fires_error() -> None:
    """assess routes flake_suspected → test, test is in cycle through merge, no context → ERROR."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    # No test_stdout/test_stderr capture
                },
                on_result=_merge_on_result(),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                        # Only 3 args — no context
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="merge",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "flake-suspected-unwinnable-loop"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)


def test_flake_suspected_to_merge_with_context_is_clean() -> None:
    """Same merge cycle but assess forwards failure context → no finding."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    "merge_test_stdout": "${{ result.test_stdout }}",
                    "merge_test_stderr": "${{ result.test_stderr }}",
                },
                on_result=_merge_on_result(),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                        " ${{ context.merge_gate_ci_conclusion }}"
                        " - ${{ context.merge_gate_diagnosis_path }}"
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="merge",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "flake-suspected-unwinnable-loop"]
    assert not flagged


def test_flake_suspected_not_in_merge_cycle_is_clean() -> None:
    """resolve_ci routes flake_suspected → re_push (no merge step in cycle) → no finding."""
    recipe = _make_recipe(
        {
            "resolve_ci": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                    ),
                    "step_name": "resolve_ci",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="re_push"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "re_push": RecipeStep(
                tool="push_to_remote",
                with_args={"clone_path": "${{ context.work_dir }}", "remote_url": "origin"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "flake-suspected-unwinnable-loop"]
    assert not flagged


@pytest.mark.parametrize(
    "recipe_name",
    ["remediation.yaml", "implementation.yaml", "implementation-groups.yaml"],
)
def test_rule_fires_for_all_affected_recipes_before_fix(recipe_name: str) -> None:
    """Before recipe fix: rule fires ERROR for all three pipeline recipes."""
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import load_recipe

    recipe_path = pkg_root() / "recipes" / recipe_name
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "flake-suspected-unwinnable-loop"]
    # After recipe fixes are applied, this should be zero.
    assert len(flagged) >= 1

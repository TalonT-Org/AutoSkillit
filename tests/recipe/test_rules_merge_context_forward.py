"""Tests for rules_merge_context.py: merge-test-gate-context-not-forwarded rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import CaptureEntrySpec, Severity, pkg_root
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-merge-context-forward",
        description="Test recipe for merge-test-gate-context-not-forwarded rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _merge_on_result_conditions() -> list[StepResultCondition]:
    return [
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


def test_merge_step_missing_test_output_capture_fires_error() -> None:
    """merge step routes test_gate but capture has no test_stdout/stderr → ERROR."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    # test_stdout and test_stderr are absent — should trigger the rule
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
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
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)


def test_merge_step_with_test_output_capture_is_clean() -> None:
    """merge step captures test_stdout and test_stderr → no finding."""
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
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
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
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert not flagged


def test_assess_step_missing_failure_context_fires_error() -> None:
    """assess invokes resolve-failures with only 3 args when reachable from merge test_gate."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    # Missing test_stdout/test_stderr
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
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
                        # Only 3 args — no failure context
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
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)


def test_assess_step_with_failure_context_is_clean() -> None:
    """assess invokes resolve-failures with 6 args including merge_gate context → no finding."""
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
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
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
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert not flagged


@pytest.mark.parametrize(
    "recipe_name",
    ["remediation.yaml", "implementation.yaml", "implementation-groups.yaml"],
)
def test_rule_does_not_fire_for_fixed_recipes(recipe_name: str) -> None:
    """Post recipe fix: rule no longer fires for the three pipeline recipes."""
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import load_recipe

    recipe_path = pkg_root() / "recipes" / recipe_name
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) == 0


def _diagnose_on_result_conditions() -> list[StepResultCondition]:
    return [
        StepResultCondition(when="result.failed_step == 'dirty_tree'", route="check_loop"),
        StepResultCondition(when="result.failed_step == 'test_gate'", route="check_loop"),
        StepResultCondition(when=None, route="done"),
    ]


def test_merge_step_missing_failed_step_capture_fires_error() -> None:
    """merge routes on result.failed_step to a chain reaching diagnose_merge_gate, but
    capture is missing result.failed_step → ERROR.
    """
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
                    # merge_failed_step is absent — should trigger the rule
                },
                on_result=StepResultRoute(conditions=_diagnose_on_result_conditions()),
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
                        StepResultCondition(when=None, route="diagnose_merge_gate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "diagnose_merge_gate": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.diagnose_merge_gate",
                    "test_stdout": "${{ context.merge_test_stdout }}",
                    "test_stderr": "${{ context.merge_test_stderr }}",
                    "output_dir": "/tmp/diagnose-merge-gate",
                    "failed_step": "${{ context.merge_failed_step }}",
                },
                capture={
                    "merge_gate_diagnosis_path": "${{ result.diagnosis_path }}",
                },
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-failed-step-not-captured"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)
    assert any("merge_failed_step" in f.message for f in flagged)


def test_merge_step_with_failed_step_capture_is_clean() -> None:
    """merge captures result.failed_step → rule does not fire."""
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
                    "merge_failed_step": "${{ result.failed_step }}",
                },
                on_result=StepResultRoute(conditions=_diagnose_on_result_conditions()),
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
                        StepResultCondition(when=None, route="diagnose_merge_gate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "diagnose_merge_gate": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.diagnose_merge_gate",
                    "test_stdout": "${{ context.merge_test_stdout }}",
                    "test_stderr": "${{ context.merge_test_stderr }}",
                    "output_dir": "/tmp/diagnose-merge-gate",
                    "failed_step": "${{ context.merge_failed_step }}",
                },
                capture={
                    "merge_gate_diagnosis_path": "${{ result.diagnosis_path }}",
                },
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-failed-step-not-captured"]
    assert not flagged


@pytest.mark.parametrize(
    "recipe_name",
    ["remediation.yaml", "implementation.yaml", "implementation-groups.yaml"],
)
def test_merge_failed_step_rule_does_not_fire_for_fixed_recipes(recipe_name: str) -> None:
    """Post recipe fix: merge-failed-step-not-captured rule does not fire on the three
    pipeline recipes (they all capture merge_failed_step and pass it through).
    """
    recipe_path = pkg_root() / "recipes" / recipe_name
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-failed-step-not-captured"]
    assert len(flagged) == 0


_TIMEOUT_CONTEXT_CAPTURES = {
    "merge_timed_out": "${{ result.timed_out }}",
    "merge_outer_timeout_seconds": "${{ result.outer_timeout_seconds }}",
    "merge_raw_output_artifact_path": "${{ result.raw_output_artifact_path }}",
}


def _assert_final_merge_forwards_timeout_context(recipe: Recipe) -> None:
    """Assert the final merge's timeout facts reach diagnose_merge_gate losslessly."""
    merge = recipe.steps["merge"]
    for context_key, result_ref in _TIMEOUT_CONTEXT_CAPTURES.items():
        capture = merge.capture[context_key]
        assert isinstance(capture, CaptureEntrySpec)
        assert capture.from_ == result_ref
        assert capture.value_type == "optional_string"

    assert merge.on_result is not None
    merge_routes = {condition.when: condition.route for condition in merge.on_result.conditions}
    assert merge_routes["result.failed_step == 'test_gate'"] == "check_merge_fix_loop"
    assert merge_routes["result.failed_step == 'post_rebase_test_gate'"] == "check_merge_fix_loop"

    loop = recipe.steps["check_merge_fix_loop"]
    assert loop.on_result is not None
    assert any(
        condition.when is None and condition.route == "diagnose_merge_gate"
        for condition in loop.on_result.conditions
    )

    diagnosis = recipe.steps["diagnose_merge_gate"]
    assert diagnosis.with_args["callable"] == "autoskillit.smoke_utils.diagnose_merge_gate"
    for context_key in _TIMEOUT_CONTEXT_CAPTURES:
        argument_name = context_key.removeprefix("merge_")
        assert diagnosis.with_args[argument_name] == f"${{{{ context.{context_key} }}}}"
        assert context_key in (diagnosis.optional_context_refs or [])


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation.yaml", "remediation.yaml", "implementation-groups.yaml"],
)
def test_final_merge_forwards_outer_timeout_context_to_diagnosis(recipe_name: str) -> None:
    """Bundled recipes preserve merge-gate outer-timeout provenance for remediation."""
    _assert_final_merge_forwards_timeout_context(load_recipe(pkg_root() / "recipes" / recipe_name))


def test_project_remediation_fable_forwards_outer_timeout_context_to_diagnosis() -> None:
    """The project-local remediation fable keeps the same merge-gate handoff shape."""
    recipe_path = Path.cwd() / ".autoskillit" / "recipes" / "remediation-fable.yaml"
    if not recipe_path.is_file():
        pytest.skip("project-local remediation fable is not installed in this worktree")

    _assert_final_merge_forwards_timeout_context(load_recipe(recipe_path))

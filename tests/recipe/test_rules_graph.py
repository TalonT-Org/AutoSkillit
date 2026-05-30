"""Tests for recipe/rules_graph.py semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for rules_graph tests."""
    return Recipe(
        name="test-rules-graph",
        description="Test recipe for graph rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


# ---------------------------------------------------------------------------
# unbounded-cycle
# ---------------------------------------------------------------------------


def test_unbounded_cycle_pure_loop_is_error() -> None:
    """A→B→A with no exit edges → ERROR finding, rule='unbounded-cycle'."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(tool="run_cmd", with_args={"cmd": "echo a"}, on_success="B"),
            "B": RecipeStep(tool="run_cmd", with_args={"cmd": "echo b"}, on_success="A"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == Severity.ERROR


def test_cycle_with_only_on_failure_exit_is_warning() -> None:
    """A→B→A but A.on_failure outside cycle → WARNING (conditional exit)."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo a"},
                on_success="B",
                on_failure="done",
            ),
            "B": RecipeStep(tool="run_cmd", with_args={"cmd": "echo b"}, on_success="A"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == Severity.WARNING


def test_cycle_with_retry_exit_is_error() -> None:
    """Cycle where retrying step's success path stays in cycle → ERROR (outer loop unbounded)."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
                retries=3,
                on_success="B",
                on_exhausted="done",
            ),
            "B": RecipeStep(tool="run_cmd", with_args={"cmd": "echo b"}, on_success="A"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == Severity.ERROR


def test_cycle_with_retry_exit_but_success_reenters_is_error() -> None:
    """A→B(retries=2, on_exhausted=done)→C→A: B exits on exhaustion but
    success path C→A re-enters the cycle. Must produce ERROR."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "timeout_seconds": 300},
                on_success="B",
                on_failure="B",
            ),
            "B": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:resolve-failures /tmp", "cwd": "/tmp"},
                retries=2,
                on_exhausted="done",
                on_success="C",
                on_failure="done",
            ),
            "C": RecipeStep(
                tool="push_to_remote",
                with_args={
                    "clone_path": "/tmp",
                    "remote_url": "https://github.com/o/r.git",
                    "branch": "b",
                },
                on_success="A",  # RE-ENTERS THE CYCLE
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) >= 1
    # Must be ERROR (has retry exit but no outer bound)
    assert any(f.severity == Severity.ERROR for f in cycle_findings)


def test_no_cycle_is_clean() -> None:
    """Linear A→B→C → no findings for unbounded-cycle."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(tool="run_cmd", with_args={"cmd": "echo a"}, on_success="B"),
            "B": RecipeStep(tool="run_cmd", with_args={"cmd": "echo b"}, on_success="C"),
            "C": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert cycle_findings == []


def test_cycle_with_on_result_conditional_exit_is_clean() -> None:
    """A→B→C(on_result: max_exceeded→done, else→A) → no unbounded-cycle finding.

    The on_result conditional exit provides a structural bound because one
    route exits the cycle and one re-enters, meaning the exit condition is
    evaluated on every iteration."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "timeout_seconds": 300},
                on_success="B",
                on_failure="B",
            ),
            "B": RecipeStep(
                tool="check_repo_merge_state",
                with_args={"branch": "main", "cwd": "/tmp", "remote_url": ""},
                on_success="C",
                on_failure="done",
            ),
            "C": RecipeStep(
                tool="run_python",
                with_args={"callable": "autoskillit.smoke_utils.check_loop_iteration"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="done"
                        ),
                        StepResultCondition(route="A"),
                    ]
                ),
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert cycle_findings == []


def test_cycle_with_on_result_all_routes_in_cycle_is_still_flagged() -> None:
    """A→B(on_result: both routes inside cycle) → still flagged."""
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo a"},
                on_success="B",
                on_failure="done",
            ),
            "B": RecipeStep(
                tool="run_python",
                with_args={"callable": "some.func"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.x }} == true", route="A"),
                        StepResultCondition(route="A"),
                    ]
                ),
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1


def test_cycle_with_on_context_limit_in_cycle_and_retry_exit_is_error() -> None:
    """Cycle where on_context_limit points INTO cycle must produce ERROR despite retry exit.

    on_context_limit: A places A in _fail_targets under the old code, masking the
    on_result route real_fix→A and causing the rule to emit zero findings.
    After the fix, on_context_limit is only treated as a fail exit when it routes
    OUTSIDE the cycle — here it routes INTO the cycle, so the ERROR must be emitted.
    """
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp"},
                on_success="done",
                on_failure="B",
            ),
            "B": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:resolve-failures /tmp", "cwd": "/tmp"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.verdict }} == 'real_fix'", route="A"),
                        StepResultCondition(
                            when="${{ result.verdict }} == 'ci_only'", route="done"
                        ),
                    ]
                ),
                on_failure="done",
                on_exhausted="escalate",
                on_context_limit="A",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "escalate": RecipeStep(action="stop", message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == Severity.ERROR


def test_on_context_limit_outside_cycle_is_still_fail_target() -> None:
    """on_context_limit pointing OUTSIDE the cycle must still be treated as a fail exit.

    Ensures the fix does not over-correct: when on_context_limit routes to a step
    outside the cycle, it is still an escape edge and should not cause a false positive.
    """
    recipe = _make_recipe(
        {
            "A": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp"},
                on_success="done",
                on_failure="B",
            ),
            "B": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:resolve-failures /tmp", "cwd": "/tmp"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.verdict }} == 'real_fix'", route="A"),
                        StepResultCondition(
                            when="${{ result.verdict }} == 'ci_only'", route="done"
                        ),
                    ]
                ),
                on_failure="done",
                on_exhausted="escalate",
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "escalate": RecipeStep(action="stop", message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    assert len(cycle_findings) == 1
    assert cycle_findings[0].severity == Severity.ERROR


def test_cycle_with_loop_guard_step_is_clean() -> None:
    """A cycle containing a check_loop_iteration guard with an exit route is bounded."""
    recipe = _make_recipe(
        {
            "step_a": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:foo"},
                on_success="check_loop",
                on_failure="done",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.loop_count }}",
                    "max_iterations": "3",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true",
                            route="done",
                        ),
                        StepResultCondition(route="step_a"),
                    ]
                ),
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unbounded-cycle"]
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# on-result-missing-failure-route
# ---------------------------------------------------------------------------


def test_on_result_without_on_failure_is_error() -> None:
    """Tool step: on_result set, on_failure absent → ERROR."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_result=StepResultRoute(field="status", routes={"ok": "done"}),
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "on-result-missing-failure-route"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    assert flagged[0].step_name == "entry"


def test_python_step_on_result_without_on_failure_is_error() -> None:
    """Python step with on_result set, on_failure absent → ERROR."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(
                python="import os",
                on_result=StepResultRoute(field="status", routes={"ok": "done"}),
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "on-result-missing-failure-route"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR


def test_on_result_with_on_failure_is_clean() -> None:
    """on_result + on_failure both set → no finding."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_result=StepResultRoute(field="status", routes={"ok": "done"}),
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "on-result-missing-failure-route"]
    assert flagged == []


def test_action_step_not_flagged() -> None:
    """Action step is not a tool invocation → no finding even with on_result."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(
                action="route",
                on_result=StepResultRoute(field="status", routes={"ok": "done"}),
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "on-result-missing-failure-route"]
    assert flagged == []


# ---------------------------------------------------------------------------
# tool-step-missing-failure-route
# ---------------------------------------------------------------------------


def test_tool_step_without_on_failure_is_error() -> None:
    """A tool step with on_success but no on_failure must be flagged."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-failure-route"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    assert "start" in flagged[0].message


def test_python_step_without_on_failure_is_error() -> None:
    """A python step with on_success but no on_failure must be flagged."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(python="module.func", on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-failure-route"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR


def test_tool_step_with_on_failure_passes() -> None:
    """A tool step with both on_success and on_failure passes the rule."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-failure-route"]
    assert flagged == []


def test_constant_step_without_on_failure_passes() -> None:
    """Constant steps don't invoke tools — on_failure is not required."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(constant="value", on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-failure-route"]
    assert flagged == []


def test_action_stop_without_on_failure_passes() -> None:
    """Stop steps are terminal — on_failure is not required."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(action="stop", message="All done."),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-failure-route"]
    assert flagged == []


# ---------------------------------------------------------------------------
# tool-step-missing-success-route
# ---------------------------------------------------------------------------


def test_tool_step_without_success_route_is_warning() -> None:
    """A tool step with on_failure but no on_success/on_result is flagged."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo x"}, on_failure="escalate"
            ),
            "escalate": RecipeStep(action="stop", message="failed"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-success-route"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.WARNING


def test_tool_step_with_on_result_passes_success_route() -> None:
    """A tool step with on_result (instead of on_success) has a success route."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(route="done", when=None)]
                ),
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "escalate": RecipeStep(action="stop", message="failed"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "tool-step-missing-success-route"]
    assert flagged == []


# ---------------------------------------------------------------------------
# Terminal target expansion (escalate is valid for on_failure/on_success/on_context_limit)
# ---------------------------------------------------------------------------


def test_on_failure_escalate_is_valid_terminal() -> None:
    """on_failure: escalate should be accepted without a step named 'escalate'."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    errors = validate_recipe_structure(recipe)
    assert not any("unknown step 'escalate'" in e for e in errors)


def test_on_success_escalate_is_valid_terminal() -> None:
    """on_success: escalate should be accepted without a step named 'escalate'."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_success="escalate",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    errors = validate_recipe_structure(recipe)
    assert not any("unknown step 'escalate'" in e for e in errors)


def test_on_context_limit_escalate_is_valid_terminal() -> None:
    """on_context_limit: escalate should be accepted as a terminal target."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo x"},
                on_success="done",
                on_failure="done",
                on_context_limit="escalate",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    errors = validate_recipe_structure(recipe)
    assert not any("unknown step 'escalate'" in e for e in errors)


# ---------------------------------------------------------------------------
# push-before-audit
# ---------------------------------------------------------------------------


def test_push_reachable_without_audit_is_warning() -> None:
    """Recipe has push_to_remote reachable from entry without audit-impl → WARNING."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="push"),
            "push": RecipeStep(
                tool="push_to_remote",
                with_args={"branch": "main"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "push-before-audit"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.WARNING


def test_push_after_audit_is_clean() -> None:
    """Push only reachable via audit-impl path → no finding."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="audit"),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:audit-impl worktree plan main",
                    "cwd": "/tmp",
                },
                on_success="push",
                on_failure="done",
            ),
            "push": RecipeStep(
                tool="push_to_remote",
                with_args={"branch": "main"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "push-before-audit"]
    assert flagged == []


def test_no_push_steps_is_clean() -> None:
    """Recipe with no push_to_remote → no finding."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "push-before-audit"]
    assert flagged == []


# ---------------------------------------------------------------------------
# clone-root-as-worktree
# ---------------------------------------------------------------------------


def test_test_check_with_clone_path_as_worktree_is_error() -> None:
    """Capture from result.clone_path, use in test_check.worktree_path → ERROR."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="clone_repo",
                with_args={"source_dir": "/tmp/repo"},
                capture={"wp": "result.clone_path"},
                on_success="check",
            ),
            "check": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.wp }}", "cwd": "/tmp"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "clone-root-as-worktree"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR


def test_merge_worktree_with_clone_path_is_error() -> None:
    """Capture from result.clone_path, use in merge_worktree.worktree_path → ERROR."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="clone_repo",
                with_args={"source_dir": "/tmp/repo"},
                capture={"wp": "result.clone_path"},
                on_success="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={
                    "worktree_path": "${{ context.wp }}",
                    "base_branch": "main",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "clone-root-as-worktree"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR


def test_worktree_path_from_worktree_capture_is_clean() -> None:
    """Capture from result.worktree_path (not clone_path) → no finding."""
    recipe = _make_recipe(
        {
            "impl": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    "cwd": "/tmp",
                },
                capture={"wp": "result.worktree_path"},
                on_success="check",
                on_failure="done",
            ),
            "check": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.wp }}", "cwd": "/tmp"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "clone-root-as-worktree"]
    assert flagged == []


# ---------------------------------------------------------------------------
# merge-base-unpublished
# ---------------------------------------------------------------------------


def test_merge_base_context_var_without_push_is_error() -> None:
    """merge_worktree base_branch=${{context.my_branch}}, no push_to_remote → ERROR."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="merge"),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={
                    "worktree_path": "/tmp/wt",
                    "base_branch": "${{ context.my_branch }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-base-unpublished"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR


def test_merge_base_context_var_with_push_before_is_clean() -> None:
    """push_to_remote for same context var precedes merge_worktree → no finding."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="push"),
            "push": RecipeStep(
                tool="push_to_remote",
                with_args={"branch": "${{ context.my_branch }}"},
                on_success="merge",
                on_failure="done",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={
                    "worktree_path": "/tmp/wt",
                    "base_branch": "${{ context.my_branch }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-base-unpublished"]
    assert flagged == []


def test_merge_base_literal_is_clean() -> None:
    """base_branch='main' (literal) → no finding."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="merge"),
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
    flagged = [f for f in findings if f.rule == "merge-base-unpublished"]
    assert flagged == []


# ---------------------------------------------------------------------------
# on-result-missing-tool-output-value
# ---------------------------------------------------------------------------


def test_on_result_missing_tool_output_value_catches_terminal_catchall() -> None:
    """Recoverable tool output values falling through to a terminal step trigger WARNING."""
    recipe = _make_recipe(
        {
            "watch": RecipeStep(
                tool="wait_for_ci",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="merge", when="${{ result.conclusion }} == success"
                        ),
                        StepResultCondition(route="fail"),
                    ],
                ),
                on_failure="fail",
            ),
            "merge": RecipeStep(on_success="done"),
            "fail": RecipeStep(action="stop"),
            "done": RecipeStep(action="stop"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "on-result-missing-tool-output-value"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.WARNING for f in flagged)

"""Tests for recipe/rules_graph.py semantic rules."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultRoute


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


def test_cycle_with_retry_exit_is_clean() -> None:
    """Cycle with retries>0, tool=run_skill, and on_exhausted outside cycle → no finding."""
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
    assert cycle_findings == []


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

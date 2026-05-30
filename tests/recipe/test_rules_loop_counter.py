"""Tests for loop-counter-cross-path-sharing and loop-guard-before-verify semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _guard_step(counter_var: str, *, non_exit: str, exit_route: str) -> RecipeStep:
    return RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.smoke_utils.check_loop_iteration",
            "current_iteration": f"${{{{ context.{counter_var} }}}}",
            "max_iterations": "3",
        },
        capture={counter_var: "${{ result.next_iteration }}"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(when="${{ result.max_exceeded }} == true", route=exit_route),
                StepResultCondition(route=non_exit),
            ]
        ),
        on_failure=exit_route,
        optional_context_refs=[counter_var],
    )


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-loop-counter",
        description="Test recipe for loop counter rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


class TestLoopCounterCrossPathSharing:
    def test_shared_counter_across_disjoint_entry_paths_fires(self) -> None:
        """Two disjoint paths enter the cycle at DIFFERENT members."""
        steps = {
            "initial_test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "merge_diagnose": RecipeStep(
                tool="run_python",
                with_args={"callable": "some.diagnose"},
                on_success="fix",
                on_failure="done",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="guard",
                on_failure="done",
            ),
            "guard": _guard_step("counter", non_exit="test", exit_route="done"),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert len(sharing) == 1
        assert sharing[0].severity == Severity.ERROR
        assert sharing[0].step_name == "guard"

    def test_shared_counter_single_external_predecessor_does_not_fire(self) -> None:
        steps = {
            "merge": RecipeStep(
                tool="run_skill",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="result.failed_step == 'dirty_tree'", route="guard_a"
                        ),
                        StepResultCondition(
                            when="result.failed_step == 'rebase'", route="guard_b"
                        ),
                        StepResultCondition(route="done"),
                    ]
                ),
                on_failure="done",
            ),
            "guard_a": _guard_step("merge_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("merge_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="merge",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert sharing == []

    def test_single_entry_path_counter_does_not_fire(self) -> None:
        steps = {
            "start": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="guard",
            ),
            "guard": _guard_step("counter", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert sharing == []

    def test_bundled_recipes_no_counter_sharing(self) -> None:
        for name in ("remediation", "implementation", "implementation-groups", "merge-prs"):
            recipe = load_recipe(builtin_recipes_dir() / f"{name}.yaml")
            findings = run_semantic_rules(recipe)
            sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
            assert sharing == [], f"{name}: {[(f.step_name, f.message) for f in sharing]}"


class TestLoopGuardBeforeVerify:
    def test_guard_before_verify_fires(self) -> None:
        steps = {
            "fix": RecipeStep(
                tool="run_skill",
                on_success="guard",
                on_failure="done",
            ),
            "guard": _guard_step("counter", non_exit="test", exit_route="done"),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
        assert len(gbv) == 1
        assert gbv[0].severity == Severity.WARNING
        assert gbv[0].step_name == "guard"

    def test_verify_before_guard_does_not_fire(self) -> None:
        steps = {
            "fix": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="guard",
            ),
            "guard": _guard_step("counter", non_exit="fix", exit_route="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
        assert gbv == []

    def test_bundled_recipes_no_guard_before_verify(self) -> None:
        for name in ("remediation", "implementation", "implementation-groups", "merge-prs"):
            recipe = load_recipe(builtin_recipes_dir() / f"{name}.yaml")
            findings = run_semantic_rules(recipe)
            gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
            assert gbv == [], f"{name}: {[(f.step_name, f.message) for f in gbv]}"

"""Tests for enqueue-missing-ci-gate semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for enqueue gate rule tests."""
    return Recipe(
        name="test-enqueue-gate",
        description="Test recipe for enqueue-missing-ci-gate rule.",
        version="0.2.0",
        kitchen_rules=["Use wait_for_ci."],
        steps=steps,
    )


def test_enqueue_without_ci_gate_fires_error() -> None:
    """Recipe with step_a → enqueue_pr (no wait_for_ci) must fire ERROR."""
    steps = {
        "step_a": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hello"}),
        "enqueue_pr": RecipeStep(tool="enqueue_pr", with_args={"pr_number": "42"}),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 1
    assert gate_findings[0].severity == Severity.ERROR
    assert gate_findings[0].step_name == "enqueue_pr"
    assert "wait_for_ci" in gate_findings[0].message


def test_enqueue_with_ci_gate_passes() -> None:
    """Recipe with step_a → wait_for_ci → enqueue_pr must NOT fire the rule."""
    steps = {
        "step_a": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hello"}),
        "wait_ci": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": "600"},
        ),
        "enqueue_pr": RecipeStep(tool="enqueue_pr", with_args={"pr_number": "42"}),
    }
    # Set up routing so wait_for_ci is on the path to enqueue_pr
    steps["step_a"].on_success = "wait_ci"
    steps["wait_ci"].on_success = "enqueue_pr"

    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 0


def test_enqueue_with_ci_gate_on_all_paths_passes() -> None:
    """Recipe with a decision branch where both arms pass wait_for_ci before enqueue_pr."""
    steps = {
        "start": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hello"}),
        "route_decision": RecipeStep(action="route"),
        "wait_ci_a": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": "600"},
        ),
        "wait_ci_b": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": "600"},
        ),
        "enqueue_pr": RecipeStep(tool="enqueue_pr", with_args={"pr_number": "42"}),
    }
    steps["start"].on_success = "route_decision"
    # Both branches route through a wait_for_ci step before reaching enqueue_pr
    steps["route_decision"].on_result = StepResultRoute(
        conditions=[
            StepResultCondition(route="wait_ci_a", when="${{ result.value }} == branch_a"),
            StepResultCondition(route="wait_ci_b"),
        ]
    )
    steps["wait_ci_a"].on_success = "enqueue_pr"
    steps["wait_ci_b"].on_success = "enqueue_pr"

    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 0


def test_enqueue_with_ci_gate_on_only_one_path_fires() -> None:
    """Recipe where only one branch of a decision has wait_for_ci before enqueue_pr."""
    steps = {
        "start": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hello"}),
        "route_decision": RecipeStep(action="route"),
        "wait_ci": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": "600"},
        ),
        "enqueue_pr": RecipeStep(tool="enqueue_pr", with_args={"pr_number": "42"}),
    }
    steps["start"].on_success = "route_decision"
    # Branch A goes through wait_for_ci; branch B (default) skips directly to enqueue_pr
    steps["route_decision"].on_result = StepResultRoute(
        conditions=[
            StepResultCondition(route="wait_ci", when="${{ result.value }} == needs_ci"),
            StepResultCondition(route="enqueue_pr"),
        ]
    )
    steps["wait_ci"].on_success = "enqueue_pr"

    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 1
    assert gate_findings[0].severity == Severity.ERROR


def test_reenter_queue_without_ci_gate_fires() -> None:
    """reenter_queue step (enqueue_pr) reachable without wait_for_ci ancestor fires ERROR."""
    steps = {
        "start": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hello"}),
        "reenter_queue": RecipeStep(
            tool="enqueue_pr",
            with_args={"pr_number": "${{ context.current_pr_number }}"},
        ),
    }
    steps["start"].on_success = "reenter_queue"

    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 1
    assert gate_findings[0].step_name == "reenter_queue"


def test_bundled_merge_prs_recipe_passes_after_fix(pmp_recipe: Recipe) -> None:
    """Bundled merge-prs.yaml must pass enqueue-missing-ci-gate after the fix.

    This test will fail on the unfixed recipe (before the CI gate is added)
    and pass after the fix is applied, serving as the regression anchor.
    """
    findings = run_semantic_rules(pmp_recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 0, (
        f"merge-prs.yaml has enqueue_pr steps reachable without wait_for_ci: "
        f"{[(f.step_name, f.message) for f in gate_findings]}"
    )


def test_bundled_implementation_recipe_passes(impl_recipe: Recipe) -> None:
    """Bundled implementation.yaml must pass enqueue-missing-ci-gate (it already has CI gates)."""
    findings = run_semantic_rules(impl_recipe)
    gate_findings = [f for f in findings if f.rule == "enqueue-missing-ci-gate"]
    assert len(gate_findings) == 0

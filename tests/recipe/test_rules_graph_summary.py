"""Tests for recipe/rules/graph/rules_graph_summary.py semantic rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_REMEDIATION_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)


def _make_recipe(
    steps: dict[str, RecipeStep],
    *,
    summary: str = "",
    ingredients: dict[str, RecipeIngredient] | None = None,
) -> Recipe:
    return Recipe(
        name="test-rules-graph-summary",
        description="Test recipe for rules_graph_summary rules.",
        version="0.2.0",
        summary=summary,
        ingredients=ingredients or {},
        steps=steps,
    )


def _skill_findings(recipe: Recipe):
    return [f for f in run_semantic_rules(recipe) if f.rule == "summary-graph-divergence"]


class TestMissingWaypoint:
    def test_gated_run_skill_phase_missing_from_summary_emits_finding(self) -> None:
        recipe = _make_recipe(
            summary="start > done",
            ingredients={"do_review": RecipeIngredient(description="Run review", default="false")},
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="review",
                    on_failure="fail",
                ),
                "review": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:review x"},
                    skip_when_false="inputs.do_review",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert any(f.step_name == "review" for f in findings), (
            f"Expected a missing-waypoint finding for 'review'. "
            f"Got: {[f.message for f in findings]}"
        )


class TestOptionalMarkerAgreement:
    def test_hyphen_underscore_normalization_satisfies_gated_phase(self) -> None:
        """`(review-approach?)` satisfies a `review_approach` step after normalization."""
        recipe = _make_recipe(
            summary="start > (review-approach?) > done",
            ingredients={
                "review_approach": RecipeIngredient(description="Review", default="false")
            },
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="review_approach",
                    on_failure="fail",
                ),
                "review_approach": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:review-approach x"},
                    skip_when_false="inputs.review_approach",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        assert _skill_findings(recipe) == []

    def test_ungated_phase_shown_with_question_mark_emits_finding(self) -> None:
        recipe = _make_recipe(
            summary="start > (review?) > done",
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="review",
                    on_failure="fail",
                ),
                "review": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:review x"},
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert any(f.step_name == "review" for f in findings), (
            f"Expected a false-optional-marker finding for 'review'. "
            f"Got: {[f.message for f in findings]}"
        )

    def test_gated_phase_shown_without_question_mark_emits_finding(self) -> None:
        recipe = _make_recipe(
            summary="start > review > done",
            ingredients={"do_review": RecipeIngredient(description="Run review", default="false")},
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="review",
                    on_failure="fail",
                ),
                "review": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:review x"},
                    skip_when_false="inputs.do_review",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert any(f.step_name == "review" for f in findings), (
            f"Expected a missing-optional-marker finding for 'review'. "
            f"Got: {[f.message for f in findings]}"
        )

    def test_multi_valued_sentinel_gate_is_exempt_from_marker_check(self) -> None:
        """A step gated on a non-boolean sentinel default (e.g. 'auto') is not required
        to carry a '?' marker even though it is technically gated."""
        recipe = _make_recipe(
            summary="start > investigate > done",
            ingredients={
                "investigate": RecipeIngredient(description="Investigate", default="auto")
            },
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="investigate",
                    on_failure="fail",
                ),
                "investigate": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:investigate x"},
                    skip_when_false="inputs.investigate",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        assert _skill_findings(recipe) == []


class TestOrdering:
    def test_reversed_order_emits_ordering_finding(self) -> None:
        recipe = _make_recipe(
            summary="(alpha?) > (beta?) > done",
            ingredients={
                "run_alpha": RecipeIngredient(description="a", default="false"),
                "run_beta": RecipeIngredient(description="b", default="false"),
            },
            steps={
                "beta": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:beta x"},
                    skip_when_false="inputs.run_beta",
                    on_success="alpha",
                    on_failure="fail",
                ),
                "alpha": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:alpha x"},
                    skip_when_false="inputs.run_alpha",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert any("order" in f.message.lower() for f in findings), (
            f"Expected an ordering finding. Got: {[f.message for f in findings]}"
        )

    def test_cycle_between_matched_phases_produces_no_ordering_finding(self) -> None:
        """Phases sharing a cycle are mutually reachable — no false total order."""
        recipe = _make_recipe(
            summary="(alpha?) > (beta?) > done",
            ingredients={
                "run_alpha": RecipeIngredient(description="a", default="false"),
                "run_beta": RecipeIngredient(description="b", default="false"),
            },
            steps={
                "alpha": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:alpha x"},
                    skip_when_false="inputs.run_alpha",
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(route="beta", when="result.retry == true"),
                            StepResultCondition(route="done"),
                        ]
                    ),
                    on_failure="fail",
                ),
                "beta": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:beta x"},
                    skip_when_false="inputs.run_beta",
                    on_success="alpha",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert not any("order" in f.message.lower() for f in findings), (
            f"Cycle must not produce an ordering finding. Got: {[f.message for f in findings]}"
        )

    def test_incomparable_branch_nodes_produce_no_ordering_finding(self) -> None:
        """Phases on independent branches (neither reaches the other) impose no order."""
        recipe = _make_recipe(
            summary="split > (alpha?) > (beta?) > done",
            ingredients={
                "run_alpha": RecipeIngredient(description="a", default="false"),
                "run_beta": RecipeIngredient(description="b", default="false"),
            },
            steps={
                "split": RecipeStep(
                    tool="run_python",
                    with_args={"callable": "pkg.split"},
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(route="alpha", when="result.branch == a"),
                            StepResultCondition(route="beta"),
                        ]
                    ),
                    on_failure="fail",
                ),
                "alpha": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:alpha x"},
                    skip_when_false="inputs.run_alpha",
                    on_success="done",
                    on_failure="fail",
                ),
                "beta": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:beta x"},
                    skip_when_false="inputs.run_beta",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        assert not any("order" in f.message.lower() for f in findings), (
            f"Incomparable branches must not produce an ordering finding. "
            f"Got: {[f.message for f in findings]}"
        )


class TestTerminationAndDedup:
    def test_branching_and_cyclic_graph_terminates_without_findings(self) -> None:
        recipe = _make_recipe(
            summary="(alpha?) > done",
            ingredients={"run_alpha": RecipeIngredient(description="a", default="false")},
            steps={
                "alpha": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:alpha x"},
                    skip_when_false="inputs.run_alpha",
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(route="loop_a", when="result.retry == true"),
                            StepResultCondition(route="done"),
                        ]
                    ),
                    on_failure="fail",
                ),
                "loop_a": RecipeStep(
                    tool="run_cmd",
                    with_args={"cmd": "echo retry"},
                    on_success="loop_b",
                    on_failure="fail",
                ),
                "loop_b": RecipeStep(
                    tool="run_cmd",
                    with_args={"cmd": "echo retry2"},
                    on_success="alpha",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        assert _skill_findings(recipe) == []

    def test_findings_deduplicated_by_step_and_reason(self) -> None:
        recipe = _make_recipe(
            summary="start > done",
            ingredients={"do_review": RecipeIngredient(description="Run review", default="false")},
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:start x"},
                    on_success="review",
                    on_failure="fail",
                ),
                "review": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:review x"},
                    skip_when_false="inputs.do_review",
                    on_success="done",
                    on_failure="fail",
                ),
                "done": RecipeStep(action="stop", message="done"),
                "fail": RecipeStep(action="stop", message="fail"),
            },
        )
        findings = _skill_findings(recipe)
        review_findings = [f for f in findings if f.step_name == "review"]
        assert len(review_findings) == 1


def test_bundled_remediation_recipe_produces_no_finding() -> None:
    recipe = load_recipe(_REMEDIATION_PATH)
    findings = _skill_findings(recipe)
    assert findings == [], (
        f"Corrected remediation.yaml must produce no summary-graph-divergence finding. "
        f"Got: {[(f.step_name, f.message) for f in findings]}"
    )

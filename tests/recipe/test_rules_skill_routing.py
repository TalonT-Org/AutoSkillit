"""Tests for skill-result-routing-gap and contract allowed_values rules."""

from __future__ import annotations

import pytest

from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-skill-routing",
        description="Test recipe for skill-result-routing-gap rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


# ---------------------------------------------------------------------------
# skill-result-routing-gap
# ---------------------------------------------------------------------------


def test_merge_pr_step_flags_merged_false_not_routed() -> None:
    """merge-prs.yaml: merge_pr step has merged=false guard — no finding expected."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    findings = run_semantic_rules(recipe)
    skill_routing_findings = [
        f for f in findings if f.rule == "skill-result-routing-gap" and f.step_name == "merge_pr"
    ]
    assert len(skill_routing_findings) == 0, (
        "merge_pr step in merge-prs.yaml must not trigger skill-result-routing-gap"
    )


def test_skill_result_routing_gap_passes_when_all_values_routed() -> None:
    """Recipe where run_skill step routes all allowed_values explicitly must not fire the rule."""
    steps = {
        "merge": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:merge-pr pr123 simple"},
            capture={"merged": "${{ result.merged }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.merged }} == true",
                    ),
                    StepResultCondition(
                        route="escalate",
                        when="${{ result.merged }} == false",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="merged"),
        "escalate": RecipeStep(action="stop", message="not merged"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    skill_routing_findings = [f for f in findings if f.rule == "skill-result-routing-gap"]
    assert len(skill_routing_findings) == 0, (
        "skill-result-routing-gap must not fire when all allowed values are explicitly routed"
    )


def test_skill_result_routing_gap_fires_when_catchall_goes_to_success() -> None:
    """Rule must fire when unrouted value falls through to a non-terminal catch-all."""
    steps = {
        "merge": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:merge-pr pr123 simple"},
            capture={"merged": "${{ result.merged }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.merged }} == true",
                    ),
                    StepResultCondition(
                        route="next_pr",
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="merged"),
        "next_pr": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    skill_routing_findings = [f for f in findings if f.rule == "skill-result-routing-gap"]
    assert len(skill_routing_findings) >= 1, (
        "skill-result-routing-gap must fire when catch-all routes to non-terminal"
    )


def test_skill_result_routing_gap_does_not_fire_for_terminal_catchall() -> None:
    """Rule must not fire when the catch-all routes to a terminal (action=stop)."""
    steps = {
        "merge": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:merge-pr pr123 simple"},
            capture={"merged": "${{ result.merged }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="done",
                        when="${{ result.merged }} == true",
                    ),
                    StepResultCondition(
                        route="stopped",  # catch-all routes to terminal — no silent fallthrough
                    ),
                ]
            ),
        ),
        "done": RecipeStep(action="stop", message="merged"),
        "stopped": RecipeStep(action="stop", message="not merged"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    skill_routing_findings = [f for f in findings if f.rule == "skill-result-routing-gap"]
    assert len(skill_routing_findings) == 0, (
        "skill-result-routing-gap must not fire when catch-all routes to terminal"
    )


# ---------------------------------------------------------------------------
# pass_through annotation
# ---------------------------------------------------------------------------


def test_pass_through_capture_skips_routing_gap() -> None:
    """A pass_through-annotated capture should not trigger routing gap."""
    steps = {
        "review": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:review-design plan.md scope.md"},
            capture={
                "experiment_type": "${{ result.experiment_type }}",
                "verdict": "${{ result.verdict }}",
            },
            pass_through=["experiment_type"],
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="next", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="revise", when="${{ result.verdict }} == REVISE"),
                    StepResultCondition(route="stopped", when="${{ result.verdict }} == STOP"),
                    StepResultCondition(route="next"),
                ]
            ),
        ),
        "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
        "revise": RecipeStep(tool="run_cmd", with_args={"cmd": "echo revise"}),
        "stopped": RecipeStep(action="stop", message="stopped"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    routing_gaps = [f for f in findings if f.rule == "skill-result-routing-gap"]
    assert routing_gaps == [], f"Unexpected routing gap findings: {routing_gaps}"


def test_routing_gap_still_fires_without_pass_through() -> None:
    """Without pass_through, unrouted allowed_values should still fire."""
    steps = {
        "review": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:review-design plan.md scope.md"},
            capture={
                "experiment_type": "${{ result.experiment_type }}",
                "verdict": "${{ result.verdict }}",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="next", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="revise", when="${{ result.verdict }} == REVISE"),
                    StepResultCondition(route="stopped", when="${{ result.verdict }} == STOP"),
                    StepResultCondition(route="next"),
                ]
            ),
        ),
        "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
        "revise": RecipeStep(tool="run_cmd", with_args={"cmd": "echo revise"}),
        "stopped": RecipeStep(action="stop", message="stopped"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    routing_gaps = [f for f in findings if f.rule == "skill-result-routing-gap"]
    assert len(routing_gaps) >= 1, "routing gap must fire for unrouted experiment_type"
    assert any("experiment_type" in f.message for f in routing_gaps)


def test_pass_through_validity_fires_on_uncaptured_output() -> None:
    """pass_through referencing an output that is not captured should warn."""
    steps = {
        "review": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:review-design plan.md scope.md"},
            capture={"verdict": "${{ result.verdict }}"},
            pass_through=["experiment_type"],
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="next", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="next"),
                ]
            ),
        ),
        "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    validity_findings = [f for f in findings if f.rule == "pass-through-validity"]
    assert len(validity_findings) >= 1, "pass-through-validity must warn on uncaptured output"
    assert any("experiment_type" in f.message for f in validity_findings)


def test_pass_through_validity_fires_on_routing_output() -> None:
    """pass_through referencing an output used in when clause should warn."""
    steps = {
        "review": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:review-design plan.md scope.md"},
            capture={
                "experiment_type": "${{ result.experiment_type }}",
                "verdict": "${{ result.verdict }}",
            },
            pass_through=["experiment_type", "verdict"],
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(route="next", when="${{ result.verdict }} == GO"),
                    StepResultCondition(route="next"),
                ]
            ),
        ),
        "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    validity_findings = [f for f in findings if f.rule == "pass-through-validity"]
    assert len(validity_findings) >= 1, (
        "pass-through-validity must warn when output controls routing"
    )
    assert any("verdict" in f.message for f in validity_findings)


# ---------------------------------------------------------------------------
# Contract: allowed_values on merged output
# ---------------------------------------------------------------------------


def test_merge_pr_contract_declares_allowed_values_for_merged() -> None:
    """The merge-pr skill contract must declare allowed_values for the merged output."""
    manifest = load_bundled_manifest()
    merge_pr_skill = manifest.get("skills", {}).get("merge-pr", {})
    outputs = merge_pr_skill.get("outputs", [])
    merged_output = next((o for o in outputs if o.get("name") == "merged"), None)
    assert merged_output is not None, "merge-pr skill must have a merged output"
    assert "allowed_values" in merged_output, "merged output must declare allowed_values"
    assert merged_output["allowed_values"] == ["true", "false"], (
        f"merged allowed_values must be ['true', 'false'], got {merged_output['allowed_values']}"
    )

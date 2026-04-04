"""Tests for the unrouted-verdict-value semantic rule.

Verifies that recipe steps using skills with declared allowed_values must
explicitly handle every allowed value in their on_result block — no value
may silently fall through a catch-all condition.
"""

from __future__ import annotations

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _catchall_steps() -> dict[str, RecipeStep]:
    """Steps where needs_human falls through the catch-all — must fail validation."""
    return {
        "review_pr": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:review-pr main main"},
            capture={"verdict": "${{ result.verdict }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="fix",
                        when="${{ result.verdict }} == changes_requested",
                    ),
                    StepResultCondition(
                        route="done",
                        when="true",  # needs_human silently falls here
                    ),
                ]
            ),
            on_failure="fix",
        ),
        "fix": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:resolve-review main main"},
            on_success="done",
        ),
        "done": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:smoke-task"},
        ),
    }


def _explicit_all_steps() -> dict[str, RecipeStep]:
    """Steps with explicit conditions for all allowed verdict values — must pass."""
    steps = _catchall_steps()
    review_step = steps["review_pr"]
    existing = review_step.on_result.conditions
    review_step.on_result.conditions = [
        existing[0],
        StepResultCondition(
            route="escalate",
            when="${{ result.verdict }} == needs_human",
        ),
        existing[1],
    ]
    steps["escalate"] = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:smoke-task"},
        on_success="done",
    )
    return steps


def test_unrouted_verdict_value_fires_when_needs_human_not_explicitly_routed() -> None:
    """Rule must error when needs_human lacks an explicit on_result condition."""
    findings = run_semantic_rules(_make_recipe(_catchall_steps()))
    rule_names = [f.rule for f in findings]
    assert "unrouted-verdict-value" in rule_names, (
        "run_semantic_rules must emit 'unrouted-verdict-value' finding when "
        "'needs_human' falls through the catch-all in review_pr on_result."
    )


def test_unrouted_verdict_value_passes_when_all_verdicts_explicitly_routed() -> None:
    """Rule must not fire when all allowed verdict values have explicit conditions."""
    findings = run_semantic_rules(_make_recipe(_explicit_all_steps()))
    rule_names = [f.rule for f in findings]
    assert "unrouted-verdict-value" not in rule_names, (
        "unrouted-verdict-value must not fire when all allowed verdict values "
        "have explicit on_result conditions."
    )


def test_unrouted_verdict_value_reports_correct_step_name() -> None:
    """Rule finding must identify the step name containing the routing gap."""
    findings = run_semantic_rules(_make_recipe(_catchall_steps()))
    verdict_findings = [f for f in findings if f.rule == "unrouted-verdict-value"]
    assert len(verdict_findings) >= 1
    finding = verdict_findings[0]
    assert finding.step_name == "review_pr", (
        f"Finding must reference step 'review_pr', got '{finding.step_name}'"
    )


def test_unrouted_verdict_value_names_the_missing_value() -> None:
    """Rule finding message must identify which verdict value is unrouted."""
    findings = run_semantic_rules(_make_recipe(_catchall_steps()))
    verdict_findings = [f for f in findings if f.rule == "unrouted-verdict-value"]
    assert len(verdict_findings) >= 1
    messages = " ".join(f.message for f in verdict_findings)
    assert "needs_human" in messages, (
        "Finding message must name 'needs_human' as the unrouted verdict value."
    )


def test_unrouted_verdict_value_severity_is_error() -> None:
    """Rule must emit ERROR severity, not WARNING."""
    findings = run_semantic_rules(_make_recipe(_catchall_steps()))
    verdict_findings = [f for f in findings if f.rule == "unrouted-verdict-value"]
    assert len(verdict_findings) >= 1
    for finding in verdict_findings:
        assert finding.severity == Severity.ERROR, (
            f"unrouted-verdict-value must be ERROR severity, got {finding.severity}"
        )


def test_unrouted_verdict_passes_for_review_design_in_research_recipe() -> None:
    """The unrouted-verdict-value rule must not fire for review-design in research.yaml.

    All three verdict values (GO, REVISE, STOP) have explicit on_result conditions.
    This test ensures the rule exercises review-design, not just review-pr.
    """
    recipe = load_recipe(builtin_recipes_dir() / "research.yaml")
    findings = run_semantic_rules(recipe)
    unrouted = [
        f
        for f in findings
        if f.rule == "unrouted-verdict-value" and f.step_name == "review_design"
    ]
    assert not unrouted, f"unrouted-verdict-value fired for review_design step: {unrouted}"

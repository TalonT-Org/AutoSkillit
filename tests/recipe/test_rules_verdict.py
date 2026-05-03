"""Tests for verdict semantic rules: unrouted-verdict-value and verdict-routing-asymmetry.

Verifies that recipe steps using skills with declared allowed_values must
explicitly handle every allowed value in their on_result block — no value
may silently fall through a catch-all condition.

Also verifies that steps invoking the same skill must route the same verdict
value to the same outcome category (continuation vs escalation).
"""

from __future__ import annotations

import pytest

import autoskillit.recipe.rules.rules_verdict as _rv
from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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
        existing[0],  # changes_requested → fix
        StepResultCondition(
            route="fix",
            when="${{ result.verdict }} == approved_with_comments",
        ),
        StepResultCondition(
            route="done",
            when="${{ result.verdict }} == approved",
        ),
        StepResultCondition(
            route="escalate",
            when="${{ result.verdict }} == needs_human",
        ),
        existing[1],  # true → done (catch-all)
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


# ---------------------------------------------------------------------------
# verdict-routing-asymmetry rule tests
# ---------------------------------------------------------------------------


def _asymmetric_resolve_steps() -> dict[str, RecipeStep]:
    """Two steps invoking the same skill, routing flake_suspected differently."""
    return {
        "fix": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:resolve-failures wp pp bb"},
            capture={"verdict": "${{ result.verdict }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="test_step",
                        when="${{ result.verdict }} == 'flake_suspected'",
                    ),
                    StepResultCondition(
                        route="failure_step",
                        when="${{ result.verdict }} == 'ci_only_failure'",
                    ),
                    StepResultCondition(
                        route="push_step",
                        when="${{ result.verdict }} == 'real_fix'",
                    ),
                    StepResultCondition(
                        route="rebase_step",
                        when="${{ result.verdict }} == 'already_green'",
                    ),
                ]
            ),
            on_failure="failure_step",
        ),
        "resolve_ci": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:resolve-failures wp pp bb"},
            capture={"verdict": "${{ result.verdict }}"},
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="failure_step",
                        when="${{ result.verdict }} == 'flake_suspected'",
                    ),
                    StepResultCondition(
                        route="failure_step",
                        when="${{ result.verdict }} == 'ci_only_failure'",
                    ),
                    StepResultCondition(
                        route="push_step",
                        when="${{ result.verdict }} == 'real_fix'",
                    ),
                    StepResultCondition(
                        route="rebase_step",
                        when="${{ result.verdict }} == 'already_green'",
                    ),
                ]
            ),
            on_failure="failure_step",
        ),
        "test_step": RecipeStep(tool="test_check"),
        "push_step": RecipeStep(tool="push_to_remote"),
        "rebase_step": RecipeStep(tool="run_cmd"),
        "failure_step": RecipeStep(tool="run_cmd"),
    }


def _consistent_resolve_steps() -> dict[str, RecipeStep]:
    """Two steps invoking the same skill, routing flake_suspected consistently."""
    steps = _asymmetric_resolve_steps()
    # Make resolve_ci route flake_suspected to test_step (continuation), matching fix
    steps["resolve_ci"].on_result.conditions[0] = StepResultCondition(
        route="test_step",
        when="${{ result.verdict }} == 'flake_suspected'",
    )
    return steps


def _different_skills_steps() -> dict[str, RecipeStep]:
    """Two steps invoking different skills — asymmetry should be ignored."""
    steps = _asymmetric_resolve_steps()
    # Change resolve_ci to invoke resolve-review instead
    steps["resolve_ci"] = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:resolve-review fb bb"},
        capture={"verdict": "${{ result.verdict }}"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    route="failure_step",
                    when="${{ result.verdict }} == 'flake_suspected'",
                ),
                StepResultCondition(
                    route="failure_step",
                    when="${{ result.verdict }} == 'ci_only_failure'",
                ),
                StepResultCondition(
                    route="push_step",
                    when="${{ result.verdict }} == 'real_fix'",
                ),
                StepResultCondition(
                    route="rebase_step",
                    when="${{ result.verdict }} == 'already_green'",
                ),
            ]
        ),
        on_failure="failure_step",
    )
    return steps


def test_verdict_routing_asymmetry_fires_on_inconsistent_routes() -> None:
    """Rule must error when same skill routes same verdict to different outcome categories."""
    findings = run_semantic_rules(_make_recipe(_asymmetric_resolve_steps()))
    rule_names = [f.rule for f in findings]
    assert "verdict-routing-asymmetry" in rule_names, (
        "run_semantic_rules must emit 'verdict-routing-asymmetry' finding when "
        "'flake_suspected' routes to continuation in fix but escalation in resolve_ci."
    )


def test_verdict_routing_asymmetry_passes_when_consistent() -> None:
    """Rule must not fire when all steps route same verdict to same outcome category."""
    findings = run_semantic_rules(_make_recipe(_consistent_resolve_steps()))
    rule_names = [f.rule for f in findings]
    assert "verdict-routing-asymmetry" not in rule_names, (
        "verdict-routing-asymmetry must not fire when both steps route "
        "'flake_suspected' to continuation steps."
    )


def test_verdict_routing_asymmetry_ignores_different_skills() -> None:
    """Rule must not fire when different skills route same verdict differently."""
    findings = run_semantic_rules(_make_recipe(_different_skills_steps()))
    rule_names = [f.rule for f in findings]
    assert "verdict-routing-asymmetry" not in rule_names, (
        "verdict-routing-asymmetry must not compare routing across different skills."
    )


def test_verdict_routing_asymmetry_reports_correct_step_names() -> None:
    """Rule finding message must include both step names involved in the asymmetry."""
    findings = run_semantic_rules(_make_recipe(_asymmetric_resolve_steps()))
    verdict_findings = [f for f in findings if f.rule == "verdict-routing-asymmetry"]
    assert len(verdict_findings) >= 1
    message = verdict_findings[0].message
    assert "fix" in message and "resolve_ci" in message, (
        f"Finding message must reference both step names, got: {message}"
    )


def test_verdict_routing_asymmetry_severity_is_error() -> None:
    """Rule must emit ERROR severity."""
    findings = run_semantic_rules(_make_recipe(_asymmetric_resolve_steps()))
    verdict_findings = [f for f in findings if f.rule == "verdict-routing-asymmetry"]
    assert len(verdict_findings) >= 1
    for finding in verdict_findings:
        assert finding.severity == Severity.ERROR, (
            f"verdict-routing-asymmetry must be ERROR severity, got {finding.severity}"
        )


# ---------------------------------------------------------------------------
# on-result-values-in-allowed-values rule tests
# ---------------------------------------------------------------------------


def _make_review_pr_recipe(
    verdict_route: str,
    allowed_values: list[str],
) -> tuple[Recipe, dict]:
    """Helper: recipe routing verdict_route with a manifest restricting allowed_values."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "review-pr": {
                "inputs": [],
                "outputs": [
                    {
                        "name": "verdict",
                        "type": "string",
                        "allowed_values": allowed_values,
                    }
                ],
            }
        },
    }
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr main main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="fix",
                            when=f"${{{{ result.verdict }}}} == {verdict_route}",
                        ),
                    ]
                ),
                on_failure="fix",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:smoke-task"},
            ),
        }
    )
    return recipe, manifest


def test_on_result_values_in_allowed_values_fires_on_unregistered_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on-result-values-in-allowed-values fires ERROR when recipe routes an unlisted value."""
    recipe, manifest = _make_review_pr_recipe(
        verdict_route="approved_with_comments",
        allowed_values=["approved", "changes_requested"],
    )
    monkeypatch.setattr(_rv, "load_bundled_manifest", lambda: manifest)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "on-result-values-in-allowed-values"]
    assert len(rule_findings) >= 1, (
        "on-result-values-in-allowed-values must fire when recipe routes "
        "'approved_with_comments' but it is not in allowed_values"
    )
    assert rule_findings[0].severity == Severity.ERROR
    assert "approved_with_comments" in rule_findings[0].message


def test_on_result_values_in_allowed_values_passes_when_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on-result-values-in-allowed-values does not fire when all routed values are allowed."""
    recipe, manifest = _make_review_pr_recipe(
        verdict_route="approved",
        allowed_values=["approved", "changes_requested", "approved_with_comments", "needs_human"],
    )
    monkeypatch.setattr(_rv, "load_bundled_manifest", lambda: manifest)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "on-result-values-in-allowed-values"]
    assert rule_findings == [], (
        "on-result-values-in-allowed-values must not fire when all routed values "
        "are present in allowed_values"
    )


def test_on_result_values_in_allowed_values_ignores_non_verdict_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on-result-values-in-allowed-values must not fire for catch-all or non-output conditions."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "review-pr": {
                "inputs": [],
                "outputs": [
                    {
                        "name": "verdict",
                        "type": "string",
                        "allowed_values": ["approved", "changes_requested"],
                    }
                ],
            }
        },
    }
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr main main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="fix",
                            when="${{ result.verdict }} == changes_requested",  # in allowed_values
                        ),
                        StepResultCondition(
                            route="done",
                            when="true",  # catch-all, no verdict value extracted
                        ),
                    ]
                ),
                on_failure="fix",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:smoke-task"},
            ),
            "done": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:smoke-task"},
            ),
        }
    )
    monkeypatch.setattr(_rv, "load_bundled_manifest", lambda: manifest)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "on-result-values-in-allowed-values"]
    assert rule_findings == [], (
        "on-result-values-in-allowed-values must not fire for catch-all 'when: true' "
        "conditions that don't reference a specific verdict value"
    )

"""Tests for commit-guard-regression-route-missing semantic validation rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-rules-commit-guard-regression-route",
        description="Test recipe for commit_guard regression routing rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _commit_guard_step(
    *, base_branch: str, on_result: StepResultRoute | None = None
) -> RecipeStep:
    return RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.recipe._cmd_rpc.commit_guard",
            "worktree_path": "${{ context.worktree_path }}",
            "base_branch": base_branch,
        },
        on_result=on_result,
    )


class TestCommitGuardRegressionRoute:
    """Tests for commit-guard-regression-route-missing rule."""

    def test_no_on_result_with_base_branch_fires(self) -> None:
        """commit_guard with base_branch and no on_result must fire ERROR."""
        recipe = _make_recipe({"commit_guard": _commit_guard_step(base_branch="main")})
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "commit-guard-regression-route-missing"]
        assert len(rule_findings) == 1
        assert rule_findings[0].severity == Severity.ERROR
        assert rule_findings[0].step_name == "commit_guard"

    def test_on_result_with_regression_detected_routing_no_finding(self) -> None:
        """commit_guard with proper regression_detected routing must not fire."""
        on_result = StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.committed }} == regression_detected",
                    route="release_issue_failure",
                ),
            ]
        )
        recipe = _make_recipe(
            {"commit_guard": _commit_guard_step(base_branch="main", on_result=on_result)}
        )
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "commit-guard-regression-route-missing"]
        assert rule_findings == []

    def test_empty_base_branch_no_finding(self) -> None:
        """commit_guard with empty base_branch must not fire (regression check is inert)."""
        recipe = _make_recipe({"commit_guard": _commit_guard_step(base_branch="")})
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "commit-guard-regression-route-missing"]
        assert rule_findings == []

    def test_non_commit_guard_step_not_flagged(self) -> None:
        """Non-commit_guard run_python step with base_branch must not fire the rule."""
        step = RecipeStep(
            tool="run_python",
            with_args={
                "callable": "some.other.callable",
                "worktree_path": "/x",
                "base_branch": "main",
            },
        )
        recipe = _make_recipe({"other": step})
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "commit-guard-regression-route-missing"]
        assert rule_findings == []

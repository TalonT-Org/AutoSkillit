"""Tests for success-stop-reason-uniqueness semantic validation rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

RULE_NAME = "success-stop-reason-uniqueness"


class TestSuccessStopReasonUniqueness:
    def test_duplicate_success_reasons_fires(self) -> None:
        """Two stop steps with same success=true reason produces a finding."""
        recipe = _make_workflow(
            {
                "done_a": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done"}'),
                },
                "done_b": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done"}'),
                },
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done_a",
                    "on_failure": "done_b",
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) >= 1
        assert any(f.severity == Severity.ERROR for f in matched)

    def test_unique_success_reasons_no_finding(self) -> None:
        """Two stop steps with distinct success=true reasons produces no finding."""
        recipe = _make_workflow(
            {
                "done_a": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done_a"}'),
                },
                "done_b": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done_b"}'),
                },
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done_a",
                    "on_failure": "done_b",
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) == 0

    def test_duplicate_failure_reasons_no_finding(self) -> None:
        """Two stop steps with same success=false reason produces no finding."""
        recipe = _make_workflow(
            {
                "fail_a": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": false, "reason": "failed"}'),
                },
                "fail_b": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": false, "reason": "failed"}'),
                },
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "fail_a",
                    "on_failure": "fail_b",
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) == 0

    def test_no_shared_ancestor_fires_warning(self) -> None:
        """Duplicate reasons without shared ancestor fires WARNING, not ERROR."""
        recipe = _make_workflow(
            {
                "done_a": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done"}'),
                },
                "done_b": {
                    "action": "stop",
                    "message": ('Emit sentinel: {"success": true, "reason": "done"}'),
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) >= 1
        assert all(f.severity == Severity.WARNING for f in matched)


class TestBundledRecipesNoConvergence:
    @pytest.mark.parametrize(
        "recipe_name",
        [
            "implementation",
            "remediation",
            "implementation-groups",
            "merge-prs",
        ],
    )
    def test_bundled_recipe_has_unique_success_reasons(self, recipe_name: str) -> None:
        """All bundled dispatchable recipes must have unique success-stop reasons."""
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert matched == [], (
            f"{recipe_name}.yaml has convergent success-stop reasons: "
            f"{[(f.step_name, f.message) for f in matched]}"
        )

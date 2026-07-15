"""Tests for the run-skill-missing-rate-limit semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules, validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestOnRateLimitField:
    """Tests for on_rate_limit as a routing field, mirroring on_context_limit behavior."""

    def test_on_rate_limit_invalid_target_raises_validation_error(self) -> None:
        """on_rate_limit must reference a declared step name."""

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_rate_limit="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe_structure(recipe)
        assert errors, "Expected validation errors for unknown on_rate_limit target"
        assert any("on_rate_limit" in e for e in errors)

    def test_on_rate_limit_valid_target_passes_validation(self) -> None:
        """on_rate_limit referencing a valid step passes validation."""

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_rate_limit="retry_worktree",
                    retries=0,
                    with_args={"skill_command": "/autoskillit:implement-worktree-no-merge x"},
                ),
                "retry_worktree": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={"skill_command": "/autoskillit:retry-worktree x y"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe_structure(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_on_rate_limit_escalate_reserved_passes_validation(self) -> None:
        """on_rate_limit: 'escalate' is reserved — passes validation without an escalate step."""

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="done",
                    on_rate_limit="escalate",
                    with_args={"skill_command": "/autoskillit:investigate x"},
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe_structure(recipe)
        assert not errors, f"Expected no errors but got: {errors}"


class TestRunSkillMissingRateLimit:
    """Tests for run-skill-missing-rate-limit semantic rule."""

    def test_run_skill_without_on_rate_limit_produces_warning(self) -> None:
        """A run_skill step without on_rate_limit must produce a WARNING."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "resolve_review": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    retries=2,
                    with_args={"skill_command": "/autoskillit:resolve-review x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert any(
            f.rule == "run-skill-missing-rate-limit" and f.step_name == "resolve_review"
            for f in warnings
        ), f"Expected run-skill-missing-rate-limit warning, got: {[f.to_dict() for f in findings]}"

    def test_run_skill_with_on_rate_limit_produces_no_warning(self) -> None:
        """A run_skill step with on_rate_limit set must not produce this warning."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "resolve_review": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_rate_limit="re_push_review",
                    with_args={"skill_command": "/autoskillit:resolve-review x", "cwd": "/tmp"},
                ),
                "re_push_review": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={"skill_command": "/autoskillit:re-push-review x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        missing_rate_limit = [f for f in findings if f.rule == "run-skill-missing-rate-limit"]
        assert not missing_rate_limit, (
            f"Expected no run-skill-missing-rate-limit warning: {missing_rate_limit}"
        )

    def test_non_skill_step_without_on_rate_limit_produces_no_warning(self) -> None:
        """A non-run_skill step (e.g. push_to_remote) without on_rate_limit is fine."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "push": RecipeStep(
                    tool="push_to_remote",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        missing_rate_limit = [f for f in findings if f.rule == "run-skill-missing-rate-limit"]
        assert not missing_rate_limit

    def test_recovery_step_itself_exempt_from_warning(self) -> None:
        """A step that is an on_rate_limit target of another step needs no recovery."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "implement": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_rate_limit="retry_worktree",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "retry_worktree": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={"skill_command": "y", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        missing_rate_limit = [
            f
            for f in findings
            if f.rule == "run-skill-missing-rate-limit" and f.step_name == "retry_worktree"
        ]
        assert not missing_rate_limit, (
            "retry_worktree is an on_rate_limit target — it IS the recovery "
            "path and should not trigger the missing-rate-limit warning"
        )

    def test_terminal_step_exempt_from_warning(self) -> None:
        """A run_skill step with action=stop needs no on_rate_limit."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "implement": RecipeStep(
                    tool="run_skill",
                    action="stop",
                    message="done",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
            },
        )
        findings = run_semantic_rules(recipe)
        missing_rate_limit = [f for f in findings if f.rule == "run-skill-missing-rate-limit"]
        assert not missing_rate_limit


def test_run_skill_missing_rate_limit_is_warning_severity() -> None:
    """The missing-rate-limit rule must fire at WARNING severity."""
    recipe = Recipe(
        name="test",
        description="test",
        summary="test",
        ingredients={},
        kitchen_rules=["test"],
        steps={
            "resolve_review": RecipeStep(
                tool="run_skill",
                on_success="done",
                on_failure="cleanup",
                retries=2,
                with_args={"skill_command": "/autoskillit:resolve-review x", "cwd": "/tmp"},
            ),
            "cleanup": RecipeStep(action="stop", message="done"),
            "done": RecipeStep(action="stop", message="done"),
        },
    )
    findings = run_semantic_rules(recipe)
    rl_findings = [f for f in findings if f.rule == "run-skill-missing-rate-limit"]
    assert rl_findings, "Rule should fire"
    assert all(f.severity == Severity.WARNING for f in rl_findings), (
        f"RuleFinding severity must be WARNING. "
        f"Got: {[(f.severity, f.step_name) for f in rl_findings]}"
    )

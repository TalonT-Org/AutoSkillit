from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestOnContextLimitField:
    """Tests for on_context_limit as a routing field and cycle detection."""

    def test_on_context_limit_invalid_target_raises_validation_error(self) -> None:
        """on_context_limit must reference a declared step name."""
        from autoskillit.recipe.validator import validate_recipe

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
                    on_context_limit="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert errors, "Expected validation errors for unknown on_context_limit target"
        assert any("on_context_limit" in e for e in errors)

    def test_on_context_limit_valid_target_passes_validation(self) -> None:
        """on_context_limit referencing a valid step passes validation."""
        from autoskillit.recipe.validator import validate_recipe

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
                    on_context_limit="retry_worktree",
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
        errors = validate_recipe(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_on_exhausted_invalid_target_raises_validation_error(self) -> None:
        """on_exhausted must reference a declared step name or be a reserved terminal."""
        from autoskillit.recipe.validator import validate_recipe

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
                    on_exhausted="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert errors, "Expected validation errors for unknown on_exhausted target"

    def test_on_exhausted_escalate_reserved_passes_validation(self) -> None:
        """on_exhausted: 'escalate' is reserved — passes validation without an escalate step."""
        from autoskillit.recipe.validator import validate_recipe

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
                    on_exhausted="escalate",
                    with_args={"skill_command": "/autoskillit:investigate x"},
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_unbounded_cycle_without_retries_produces_warning(self) -> None:
        """verify → assess → verify cycle with retries=0 must produce a warning."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "assess": RecipeStep(
                    tool="run_skill",
                    on_success="verify",
                    on_failure="cleanup",
                    retries=0,
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "verify": RecipeStep(
                    tool="test_check",
                    on_success="done",
                    on_failure="assess",
                    with_args={"worktree_path": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert any(
            "unbounded" in f.message.lower() or "cycle" in f.message.lower() for f in warnings
        )

    def test_cycle_with_retries_warns_when_success_stays_in_cycle(self) -> None:
        """A cycle where the retrying step's success path stays inside the cycle
        must emit a WARNING. fix → test → fix: fix.on_success='test' re-enters
        the cycle, so the outer loop is unbounded despite the retry exit."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="test",
                    on_failure="cleanup",
                    retries=3,
                    on_exhausted="cleanup",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "test": RecipeStep(
                    tool="test_check",
                    on_success="done",
                    on_failure="fix",
                    with_args={"worktree_path": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        cycle_warnings = [
            f for f in findings if "cycle" in f.message.lower() or "unbounded" in f.message.lower()
        ]
        assert len(cycle_warnings) >= 1, "Expected a cycle WARNING but got none"
        assert any(f.severity == Severity.WARNING for f in cycle_warnings)

    def test_truly_trapped_cycle_without_exit_produces_error(self) -> None:
        """A cycle where every step's edges stay inside the cycle must produce an ERROR."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "assess": RecipeStep(
                    tool="run_skill",
                    on_success="verify",
                    on_failure="verify",
                    retries=0,
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "verify": RecipeStep(
                    tool="test_check",
                    on_success="assess",
                    on_failure="assess",
                    with_args={"worktree_path": "/tmp"},
                ),
            },
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(
            "cycle" in f.message.lower() or "unbounded" in f.message.lower() for f in errors
        )

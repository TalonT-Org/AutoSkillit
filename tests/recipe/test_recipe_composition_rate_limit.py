"""Tests for _derive_rate_limit_routes auto-derivation behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe._recipe_composition import _derive_rate_limit_routes
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe_with_steps(steps: dict[str, dict[str, Any]]) -> Recipe:
    """Build a minimal Recipe with the given step-name -> fields mapping."""
    return Recipe(
        name="test",
        description="test",
        summary="test",
        ingredients={},
        kitchen_rules=["test"],
        steps={name: RecipeStep(**fields) for name, fields in steps.items()},
    )


class TestDeriveRateLimitRoutes:
    """Pure unit tests for _derive_rate_limit_routes."""

    def test_step_with_only_on_context_limit_gets_rate_limit(self) -> None:
        """A run_skill step with on_context_limit but no on_rate_limit gets
        on_rate_limit derived from on_context_limit."""
        recipe = _recipe_with_steps(
            {
                "fix": {
                    "tool": "run_skill",
                    "on_success": "done",
                    "on_failure": "cleanup",
                    "on_context_limit": "retry_worktree",
                    "with_args": {"skill_command": "x", "cwd": "/tmp"},
                },
                "retry_worktree": {
                    "tool": "run_skill",
                    "on_success": "done",
                    "on_failure": "cleanup",
                    "with_args": {"skill_command": "y", "cwd": "/tmp"},
                },
                "cleanup": {"action": "stop", "message": "done"},
                "done": {"action": "stop", "message": "done"},
            }
        )

        recipe = _derive_rate_limit_routes(recipe)

        assert recipe.steps["fix"].on_rate_limit == "retry_worktree", (
            f"Expected on_rate_limit to be derived from on_context_limit, "
            f"got: {recipe.steps['fix'].on_rate_limit!r}"
        )

    def test_explicit_on_rate_limit_is_preserved(self) -> None:
        """A run_skill step with explicit on_rate_limit and on_context_limit
        keeps its explicit on_rate_limit value."""
        recipe = _recipe_with_steps(
            {
                "fix": {
                    "tool": "run_skill",
                    "on_success": "done",
                    "on_failure": "cleanup",
                    "on_context_limit": "ctx_recovery",
                    "on_rate_limit": "rate_recovery",
                    "with_args": {"skill_command": "x", "cwd": "/tmp"},
                },
                "cleanup": {"action": "stop", "message": "done"},
                "done": {"action": "stop", "message": "done"},
            }
        )

        recipe = _derive_rate_limit_routes(recipe)

        assert recipe.steps["fix"].on_rate_limit == "rate_recovery", (
            "Explicit on_rate_limit must be preserved when derivation runs"
        )

    def test_step_with_neither_field_left_untouched(self) -> None:
        """A run_skill step with neither on_context_limit nor on_rate_limit
        remains None for both fields."""
        recipe = _recipe_with_steps(
            {
                "fix": {
                    "tool": "run_skill",
                    "on_success": "done",
                    "on_failure": "cleanup",
                    "with_args": {"skill_command": "x", "cwd": "/tmp"},
                },
                "cleanup": {"action": "stop", "message": "done"},
                "done": {"action": "stop", "message": "done"},
            }
        )

        recipe = _derive_rate_limit_routes(recipe)

        assert recipe.steps["fix"].on_rate_limit is None, (
            "Step without on_context_limit should not get on_rate_limit derived"
        )
        assert recipe.steps["fix"].on_context_limit is None

    def test_non_skill_step_not_affected(self) -> None:
        """Non-run_skill steps are not affected by the derivation."""
        recipe = _recipe_with_steps(
            {
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "on_failure": "cleanup",
                    "on_context_limit": "ctx_recovery",
                    "with_args": {},
                },
                "cleanup": {"action": "stop", "message": "done"},
                "done": {"action": "stop", "message": "done"},
            }
        )

        recipe = _derive_rate_limit_routes(recipe)

        assert recipe.steps["push"].on_rate_limit is None, (
            "Non-run_skill steps should not get on_rate_limit derived"
        )

    def test_escalate_on_context_limit_derived_as_rate_limit(self) -> None:
        """A run_skill step with on_context_limit='escalate' gets on_rate_limit='escalate'."""
        recipe = _recipe_with_steps(
            {
                "fix": {
                    "tool": "run_skill",
                    "on_success": "done",
                    "on_failure": "done",
                    "on_context_limit": "escalate",
                    "with_args": {"skill_command": "x", "cwd": "/tmp"},
                },
                "done": {"action": "stop", "message": "done"},
            }
        )

        recipe = _derive_rate_limit_routes(recipe)

        assert recipe.steps["fix"].on_rate_limit == "escalate", (
            f"Expected on_rate_limit='escalate', got: {recipe.steps['fix'].on_rate_limit!r}"
        )


_RECIPE_YAML_MINIMAL = """\
name: test-rate-limit-derive
description: derive test
autoskillit_version: "0.2.0"
kitchen_rules:
  - no_dangerous_skip
steps:
  fix:
    tool: run_skill
    on_context_limit: retry_worktree
    on_success: done
    on_failure: cleanup
    with:
      skill_command: /autoskillit:foo bar
  retry_worktree:
    tool: run_skill
    on_success: done
    on_failure: cleanup
    with:
      skill_command: /autoskillit:bar baz
  cleanup:
    action: stop
    message: done
  done:
    action: stop
    message: done
"""


class TestDeriveRateLimitRoutesViaLoadAndValidate:
    """Integration tests verifying _derive_rate_limit_routes runs inside load_and_validate."""

    def test_load_and_validate_populates_on_rate_limit(self, tmp_path: Any) -> None:
        """After load_and_validate, a step with on_context_limit but no on_rate_limit
        in YAML has on_rate_limit populated automatically."""
        from autoskillit.recipe import _api_cache as cache_mod

        with patch.object(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache()):
            recipes_dir = tmp_path / ".autoskillit" / "recipes"
            recipes_dir.mkdir(parents=True)
            recipe_path = recipes_dir / "test-rate-limit-derive.yaml"
            recipe_path.write_text(_RECIPE_YAML_MINIMAL)

            result = load_and_validate("test-rate-limit-derive", tmp_path)

            findings = [
                f
                for f in result.get("suggestions", [])
                if isinstance(f, dict) and f.get("rule") == "run-skill-missing-rate-limit"
            ]
            assert not findings, (
                f"After auto-derivation, no run-skill-missing-rate-limit findings "
                f"should remain. Got: {findings}"
            )

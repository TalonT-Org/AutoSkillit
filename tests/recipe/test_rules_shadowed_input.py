from __future__ import annotations

import pytest

from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe, RecipeIngredient
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestShadowedRequiredInput:
    """Tests for the shadowed-required-input semantic rule."""

    def test_fires_when_required_input_in_context_but_passed_as_prose(self) -> None:
        """Rule fires when plan_path is an ingredient but skill_command passes prose text."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={
                "plan_path": RecipeIngredient(description="Plan file path", required=True),
            },
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge temp/my-plan.md"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert any(
            f.rule == "shadowed-required-input" and f.step_name == "implement" for f in findings
        ), "Expected shadowed-required-input finding when plan_path is ingredient but prose passed"

    def test_clean_when_template_ref_used(self) -> None:
        """Rule is silent when skill_command uses ${{ context.plan_path }} template reference."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={
                "plan_path": RecipeIngredient(description="Plan file path", required=True),
            },
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "shadowed-required-input" for f in findings), (
            "Expected no shadowed-required-input finding when template ref is used"
        )

    def test_clean_when_input_not_yet_in_context(self) -> None:
        """Rule is silent when plan_path is not an ingredient and not in available context."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={},  # plan_path not declared — not yet available
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge temp/my-plan.md"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "shadowed-required-input" for f in findings), (
            "Expected no shadowed-required-input finding when input is not available in context"
        )

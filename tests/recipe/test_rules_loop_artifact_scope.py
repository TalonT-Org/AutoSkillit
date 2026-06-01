"""Tests for recipe/rules_loop_artifact_scope.py semantic rule (1c)."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-loop-artifact-scope",
        description="Test recipe for loop artifact scope rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def test_rule_fires_when_run_skill_in_cycle_has_static_output_dir() -> None:
    """Rule fires ERROR when run_skill step in a cycle has a static output_dir."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-pr branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="review",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 1
    assert loop_findings[0].severity == Severity.ERROR
    assert loop_findings[0].step_name == "review"


def test_rule_does_not_fire_when_output_dir_is_iter_scoped() -> None:
    """Rule does NOT fire when output_dir includes a ${{ context. variable reference."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-pr branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.loop_count }}",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="review",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0


def test_rule_does_not_fire_for_run_python_in_cycle_with_static_output_dir() -> None:
    """Rule does NOT fire for run_python steps (they use atomic_write, not the Write tool)."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_review_loop",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                on_success="loop_back",
            ),
            "loop_back": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo back"},
                on_success="start",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0


def test_rule_does_not_fire_for_run_skill_not_in_cycle() -> None:
    """Rule does NOT fire for run_skill steps that are not in any loop cycle."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-pr branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0

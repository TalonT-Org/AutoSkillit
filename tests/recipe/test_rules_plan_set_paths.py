"""Focused reachability tests for sealed plan-set admission."""

from __future__ import annotations

import pytest

from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="plan-set-path",
        description="test",
        ingredients={"issue_url": RecipeIngredient(description="issue", required=True)},
        steps=steps,
        kitchen_rules="test",
    )


def test_on_skip_bypass_is_rejected() -> None:
    findings = run_semantic_rules(
        _recipe(
            {
                "plan": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan"},
                    capture_list={"plan_parts": "${{ result.plan_parts }}"},
                    on_skip="verify",
                    retries=0,
                ),
                "bind": RecipeStep(
                    tool="bind_plan_set",
                    with_args={"seal": "true"},
                    capture={"plan_set_authority_path": "${{ result.plan_set_authority_path }}"},
                    on_success="verify",
                ),
                "verify": RecipeStep(
                    tool="run_skill", with_args={"skill_command": "/autoskillit:dry-walkthrough"}
                ),
            }
        )
    )
    assert any(f.rule == "plan-set-authority-sealed-path" for f in findings)


def test_converged_sealed_and_unsealed_paths_are_rejected() -> None:
    findings = run_semantic_rules(
        _recipe(
            {
                "plan": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan"},
                    capture_list={"plan_parts": "${{ result.plan_parts }}"},
                    on_success="bind",
                    retries=0,
                ),
                "bind": RecipeStep(
                    tool="bind_plan_set",
                    with_args={"seal": "true"},
                    capture={"plan_set_authority_path": "${{ result.plan_set_authority_path }}"},
                    on_success="verify",
                    on_failure="verify",
                ),
                "verify": RecipeStep(
                    tool="run_skill", with_args={"skill_command": "/autoskillit:dry-walkthrough"}
                ),
            }
        )
    )
    assert any(f.rule == "plan-set-authority-sealed-path" for f in findings)


def test_salvage_only_bypass_is_rejected() -> None:
    findings = run_semantic_rules(
        _recipe(
            {
                "salvage": RecipeStep(
                    tool="run_python",
                    with_args={"callable": "autoskillit.recipe._cmd_rpc.verify_plan_artifacts"},
                    on_success="verify",
                ),
                "verify": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:dry-walkthrough"},
                ),
            }
        )
    )
    assert any(f.rule == "plan-set-authority-sealed-path" for f in findings)

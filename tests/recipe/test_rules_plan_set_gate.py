"""Issue-bearing multipart recipes must bind the sealed authority by context."""

from __future__ import annotations

import pytest

from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_SKILLS = ("implement-worktree", "implement-worktree-no-merge", "retry-worktree")
_AUTHORITY = "${{ context.plan_set_authority_path }}"


def _recipe(
    skill: str, *, binding: str | None, optional: bool = False, bind: bool = True
) -> Recipe:
    implementation_inputs = {"plan_path": "${{ context.plan_path }}"}
    if binding is not None:
        implementation_inputs["plan_set_authority_path"] = binding
    steps = {
        "plan": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:make-plan"},
            capture_list={"plan_parts": "${{ result.plan_parts }}"},
            retries=0,
            on_success="bind" if bind else "implement",
        ),
        "implement": RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": f"/autoskillit:{skill}",
                "skill_inputs": implementation_inputs,
            },
            optional_context_refs=["plan_set_authority_path"] if optional else [],
        ),
    }
    if bind:
        steps["bind"] = RecipeStep(
            tool="bind_plan_set",
            with_args={"seal": "true"},
            capture={"plan_set_authority_path": "${{ result.plan_set_authority_path }}"},
            on_success="implement",
        )
    return Recipe(
        name="plan-set-gate",
        description="test",
        ingredients={"issue_url": RecipeIngredient(description="issue", required=True)},
        steps=steps,
        kitchen_rules=["test"],
    )


@pytest.mark.parametrize("skill", _SKILLS)
@pytest.mark.parametrize("variant", ("missing", "empty", "wrong", "optional"))
def test_implementer_binding_variants_are_rejected(skill: str, variant: str) -> None:
    binding = {
        "missing": None,
        "empty": "",
        "wrong": "${{ context.other_authority_path }}",
        "optional": _AUTHORITY,
    }[variant]
    recipe = _recipe(skill, binding=binding, optional=variant == "optional")
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "plan-set-authority-not-threaded" for f in findings)


@pytest.mark.parametrize("skill", _SKILLS)
def test_implementer_correct_binding_is_accepted(skill: str) -> None:
    findings = run_semantic_rules(_recipe(skill, binding=_AUTHORITY))
    assert not any(f.rule == "plan-set-authority-not-threaded" for f in findings)


def test_missing_sealed_bind_is_rejected() -> None:
    findings = run_semantic_rules(_recipe("implement-worktree", binding=_AUTHORITY, bind=False))
    assert any(f.rule == "plan-set-authority-not-threaded" for f in findings)

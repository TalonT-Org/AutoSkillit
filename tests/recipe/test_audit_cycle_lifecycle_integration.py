"""Loaded-recipe audit authority and disposition-delivery integration."""

from __future__ import annotations

import pytest

from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_AUDIT_CONSUMERS = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
    "research-implement",
    "research",
)
_DRY_CONSUMERS = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
)


@pytest.mark.parametrize("recipe_name", _AUDIT_CONSUMERS)
def test_loaded_audit_remediation_chain_delivers_one_bound_authority(
    recipe_name: str,
) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    projection = bind_recipe(recipe)
    audit_steps = [
        (step_name, invocation)
        for step_name, invocation in projection.invocations.items()
        if invocation.skill_name == "audit-impl"
    ]
    make_plan_steps = [
        (step_name, invocation)
        for step_name, invocation in projection.invocations.items()
        if invocation.skill_name == "make-plan"
    ]

    assert audit_steps
    assert make_plan_steps
    for step_name, invocation in audit_steps:
        assert "audit_cycle_path" in recipe.steps[step_name].capture
        prior = invocation.skill_input("prior_audit_cycle_path")
        assert prior is not None and prior.is_present
        assert prior.context_dependencies == ("audit_cycle_path",)

    for step_name, invocation in make_plan_steps:
        authority = invocation.skill_input("audit_cycle_path")
        assert authority is not None and authority.is_present
        assert authority.context_dependencies == ("audit_cycle_path",)
        if step_name not in {
            "plan_ejected_rebase_conflicts",
            "plan_proactive_rebase_conflicts",
        }:
            assert "plan_disposition_path" in recipe.steps[step_name].capture

    assert not any(
        "false_positive" in str(condition.when)
        for step in recipe.steps.values()
        if step.on_result is not None
        for condition in step.on_result.conditions
    )


@pytest.mark.parametrize("recipe_name", _DRY_CONSUMERS)
def test_every_loaded_dry_child_receives_the_complete_current_tuple(
    recipe_name: str,
) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    projection = bind_recipe(recipe)
    dry_invocations = [
        invocation
        for invocation in projection.invocations.values()
        if invocation.skill_name == "dry-walkthrough"
    ]

    assert dry_invocations
    for invocation in dry_invocations:
        inputs = {value.name: value for value in invocation.skill_inputs}
        assert inputs.keys() >= {
            "plan_path",
            "audit_cycle_path",
            "plan_disposition_path",
        }
        assert inputs["plan_path"].is_present
        assert inputs["audit_cycle_path"].is_present
        assert inputs["plan_disposition_path"].is_present
        assert inputs["audit_cycle_path"].context_dependencies == ("audit_cycle_path",)
        assert inputs["plan_disposition_path"].context_dependencies == ("plan_disposition_path",)

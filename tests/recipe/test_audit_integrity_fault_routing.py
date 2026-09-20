"""Audit integrity faults have a bounded counter separate from code remediation."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_AUDIT_RECIPES = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
    "research",
    "research-implement",
)


@pytest.mark.parametrize("name", _AUDIT_RECIPES)
def test_integrity_faults_do_not_consume_code_budget(name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{name}.yaml")
    audit = recipe.steps["audit_impl"]
    routes = {
        condition.when: condition.route
        for condition in audit.on_result.conditions
        if condition.when is not None
    }
    for status in (
        "SEMANTIC_REJECTED",
        "CONFLICT",
        "STORAGE_FAILURE",
        "QUARANTINED",
        "NON_PUBLISHED_STANDALONE",
    ):
        assert any(
            status in when and route == "check_audit_integrity_retry"
            for when, route in routes.items()
        )

    integrity = recipe.steps["check_audit_integrity_retry"]
    assert integrity.with_args["current_iteration"] == (
        "${{ context.audit_integrity_fault_count }}"
    )
    assert integrity.with_args["max_iterations"] == ("${{ inputs.audit_integrity_max_retries }}")
    assert integrity.capture["audit_integrity_fault_count"].from_ == (
        "${{ result.next_iteration }}"
    )
    assert not any(
        step.with_args.get("callable") == "autoskillit.smoke_utils.init_counter"
        and "audit_integrity_fault_count" in step.capture
        for step in recipe.steps.values()
    )

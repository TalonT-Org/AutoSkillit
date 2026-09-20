"""The merge-PR audit cycle crosses a content-aware remediation bound."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_merge_prs_no_go_crosses_bounded_gate() -> None:
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    audit = recipe.steps["audit_impl"]
    no_go = {
        condition.route
        for condition in audit.on_result.conditions
        if condition.when is not None and "NO GO" in condition.when
    }
    assert no_go == {"check_audit_remediation_loop"}
    gate = recipe.steps["check_audit_remediation_loop"]
    assert gate.with_args["callable"] == "autoskillit.smoke_utils.check_audit_remediation_outcome"
    assert gate.with_args["prior_authority_path"] == "${{ context.audit_cycle_path }}"
    assert gate.with_args["current_authority_path"] == "${{ context.audit_cycle_path_raw }}"
    routes = {condition.when: condition.route for condition in gate.on_result.conditions}
    assert routes["${{ result.outcome }} == EXHAUSTED"] == "escalate_stop"
    assert routes["${{ result.outcome }} == PROGRESSING"] == "merge_audit_cycle_path"

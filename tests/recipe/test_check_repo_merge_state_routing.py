"""Regression guard: check_repo_merge_state on_failure must route to immediate_merge.

Prior to this fix, on_failure pointed to release_issue_success (a terminal success path),
which would mislabel the issue as "staged" while leaving the PR unmerged on API failure.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_check_repo_merge_state_on_failure_routes_to_immediate_merge_not_success(recipe_name):
    """check_repo_merge_state on_failure must not point to a terminal success step.

    Ensures that a network/auth failure in check_repo_merge_state falls through
    to immediate_merge (direct squash merge path) rather than release_issue_success
    (which would mislabel the issue as staged while the PR remains unmerged).
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["check_repo_merge_state"]
    assert step.on_failure == "immediate_merge", (
        f"{recipe_name}: on_failure={step.on_failure!r}, expected 'immediate_merge'"
    )

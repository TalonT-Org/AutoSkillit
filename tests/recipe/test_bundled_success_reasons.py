"""Declared success reasons in bundled recipe stop examples."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_EXPECTED_SUCCESS_REASONS = {
    "full-audit": frozenset({"completed"}),
    "implement-findings": frozenset({"implement-findings complete"}),
    "implementation": frozenset(
        {"already_done", "implementation_complete", "merge_unconfirmed", "no_changes"}
    ),
    "implementation-groups": frozenset({"implementation_groups_complete", "merge_unconfirmed"}),
    "merge-prs": frozenset({"merge complete"}),
    "planner": frozenset({"planning complete"}),
    "remediation": frozenset(
        {"already_done", "merge_unconfirmed", "no_changes", "remediation_complete"}
    ),
    "research": frozenset({"no_pr_url", "research complete"}),
    "research-archive": frozenset({"no_pr_url", "research_archive_complete"}),
}


def test_bundled_recipe_declarations_match_success_stop_examples() -> None:
    """Each bundled recipe reports only reasons present in successful stop examples."""
    recipes = [
        load_recipe(recipe_path) for recipe_path in sorted(builtin_recipes_dir().glob("*.yaml"))
    ]

    assert recipes
    assert set(_EXPECTED_SUCCESS_REASONS) <= {recipe.name for recipe in recipes}
    for recipe in recipes:
        actual = recipe.declared_success_reasons()
        assert actual == _EXPECTED_SUCCESS_REASONS.get(recipe.name), recipe.name
        if recipe.name == "research-archive":
            assert actual == frozenset({"no_pr_url", "research_archive_complete"})

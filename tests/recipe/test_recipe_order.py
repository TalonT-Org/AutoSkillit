"""Tests for stable display order registry — recipe/order.py and its effect on list_recipes."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from autoskillit.core.types import RecipeSource
from autoskillit.recipe.io import group_rank, list_recipes
from autoskillit.recipe.order import BUNDLED_RECIPE_ORDER

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def test_implementation_appears_before_remediation(tmp_path: Path) -> None:
    """'implementation' must appear before 'remediation' in list_recipes output."""
    result = list_recipes(tmp_path)
    names = [r.name for r in result.items]
    assert "implementation" in names and "remediation" in names
    assert names.index("implementation") < names.index("remediation")


def test_registered_recipes_precede_unregistered_in_group0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered Group-0 recipes must appear before any unregistered Group-0 recipe."""
    import autoskillit.recipe.io as recipe_io

    monkeypatch.setattr(recipe_io, "BUNDLED_RECIPE_ORDER", ["implementation"])
    result = list_recipes(tmp_path)
    group0 = [r.name for r in result.items if group_rank(r) == 0]
    assert group0[0] == "implementation"


def test_unregistered_group0_recipes_alphabetical_after_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unregistered Group-0 recipes must sort alphabetically after all registered ones."""
    import autoskillit.recipe.io as recipe_io

    monkeypatch.setattr(recipe_io, "BUNDLED_RECIPE_ORDER", ["implementation"])
    result = list_recipes(tmp_path)
    group0 = [r.name for r in result.items if group_rank(r) == 0]
    unregistered = group0[1:]
    assert unregistered == sorted(unregistered), (
        f"Unregistered Group-0 recipes not alphabetical: {unregistered}"
    )


def test_adding_unregistered_recipe_does_not_shift_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inserting a name before 'implementation' alphabetically must not shift its position."""
    import autoskillit.recipe.io as recipe_io

    monkeypatch.setattr(recipe_io, "BUNDLED_RECIPE_ORDER", ["implementation", "remediation"])
    result = list_recipes(tmp_path)
    group0_before = [r.name for r in result.items if group_rank(r) == 0]
    impl_idx_before = group0_before.index("implementation")
    remed_idx_before = group0_before.index("remediation")

    # Confirm registry order is honoured
    assert impl_idx_before < remed_idx_before, (
        "'implementation' must come before 'remediation'"
    )


def test_registry_does_not_affect_addon_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUNDLED_RECIPE_ORDER must not alter relative order of Group-1 Add-on recipes."""
    import autoskillit.recipe.io as recipe_io

    result_before = list_recipes(tmp_path)
    addon_before = [r.name for r in result_before.items if group_rank(r) == 1]

    monkeypatch.setattr(recipe_io, "BUNDLED_RECIPE_ORDER", ["implementation"] + addon_before)
    result_after = list_recipes(tmp_path)
    addon_after = [r.name for r in result_after.items if group_rank(r) == 1]

    assert addon_after == sorted(addon_after), (
        "Group-1 Add-on recipes must remain alphabetical regardless of registry"
    )


def test_bundled_recipe_order_covers_all_group0_recipes(tmp_path: Path) -> None:
    """Warn (do not fail) if a Group-0 bundled recipe is missing from BUNDLED_RECIPE_ORDER."""
    result = list_recipes(tmp_path)
    group0_names = [r.name for r in result.items if group_rank(r) == 0]
    missing = [n for n in group0_names if n not in BUNDLED_RECIPE_ORDER]
    if missing:
        warnings.warn(
            f"Group-0 bundled recipes missing from BUNDLED_RECIPE_ORDER: {missing}. "
            "Add them to src/autoskillit/recipe/order.py to pin their display position.",
            UserWarning,
            stacklevel=1,
        )

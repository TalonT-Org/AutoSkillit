"""Reflective ledger for every declared recipe dataclass field.

The architectural consumer guard owns the source-site proof. This contract
keeps the two owner-qualified registries visibly sorted and the union of
registries visibly covers every declared owner.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from tests.arch.test_recipe_dataclass_consumption import (
    DECLARED_RECIPE_FIELDS,
    DEFERRED_RECIPE_FIELDS,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_ledger_keys_are_sorted() -> None:
    for registry_name, registry in (
        ("DECLARED_RECIPE_FIELDS", DECLARED_RECIPE_FIELDS),
        ("DEFERRED_RECIPE_FIELDS", DEFERRED_RECIPE_FIELDS),
    ):
        keys = list(registry)
        assert keys == sorted(keys, key=lambda key: (key[0].__name__, key[1])), (
            f"{registry_name} keys must be owner/name sorted so a diff isolates "
            "exactly the changed declaration"
        )


def test_recipe_dataclass_registry_covers_all_three_owners() -> None:
    owners = {key[0] for key in DECLARED_RECIPE_FIELDS | DEFERRED_RECIPE_FIELDS}
    assert owners == {Recipe, RecipeIngredient, RecipeStep}

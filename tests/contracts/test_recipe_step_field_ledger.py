"""Reflective ledger for every declared recipe dataclass field.

The architectural consumer guard owns the source-site proof. This contract
keeps the two owner-qualified registries visibly complete and stable at review.
"""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from tests.arch.test_recipe_dataclass_consumption import (
    DECLARED_RECIPE_FIELDS,
    DEFERRED_RECIPE_FIELDS,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_KNOWN_CLASSIFICATIONS = frozenset({"execution", "composition", "validation-only"})


def _key_name(key: tuple[type[object], str]) -> tuple[str, str]:
    return (key[0].__name__, key[1])


def _live_fields() -> set[tuple[type[object], str]]:
    return {
        (owner, field.name)
        for owner in (RecipeIngredient, RecipeStep, Recipe)
        for field in dataclasses.fields(owner)
    }


def test_ledger_keys_are_sorted() -> None:
    for registry_name, registry in (
        ("DECLARED_RECIPE_FIELDS", DECLARED_RECIPE_FIELDS),
        ("DEFERRED_RECIPE_FIELDS", DEFERRED_RECIPE_FIELDS),
    ):
        keys = list(registry)
        assert keys == sorted(keys, key=_key_name), (
            f"{registry_name} keys must be owner/name sorted so a diff isolates "
            "exactly the changed declaration"
        )


def test_no_silent_recipe_dataclass_field_additions() -> None:
    missing = _live_fields() - set(DECLARED_RECIPE_FIELDS) - set(DEFERRED_RECIPE_FIELDS)
    assert not missing, f"unclassified recipe dataclass fields: {sorted(map(_key_name, missing))}"


def test_no_silent_recipe_dataclass_field_removals() -> None:
    stale = (set(DECLARED_RECIPE_FIELDS) | set(DEFERRED_RECIPE_FIELDS)) - _live_fields()
    assert not stale, (
        f"ledger entries for removed recipe dataclass fields: {sorted(map(_key_name, stale))}"
    )


def test_classifications_are_known() -> None:
    unknown = {
        _key_name(key): definition.classification
        for key, definition in DECLARED_RECIPE_FIELDS.items()
        if definition.classification not in _KNOWN_CLASSIFICATIONS
    }
    assert not unknown, f"unrecognized recipe field classification(s): {unknown}"


def test_recipe_dataclass_registry_covers_all_three_owners() -> None:
    owners = {key[0] for key in DECLARED_RECIPE_FIELDS | DEFERRED_RECIPE_FIELDS}
    assert owners == {RecipeIngredient, RecipeStep, Recipe}

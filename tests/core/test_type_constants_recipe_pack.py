"""Tests for RECIPE_PACK_REGISTRY + RecipePackDef (franchise schema extension)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_recipe_pack_def_namedtuple_fields() -> None:
    from autoskillit.core import RecipePackDef

    rpd = RecipePackDef(default_enabled=True, description="test pack")
    assert rpd.default_enabled is True
    assert rpd.description == "test pack"


def test_recipe_pack_registry_has_three_entries() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY, RecipePackDef

    assert len(RECIPE_PACK_REGISTRY) == 3
    assert all(isinstance(v, RecipePackDef) for v in RECIPE_PACK_REGISTRY.values())


def test_recipe_pack_registry_implementation_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "implementation-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["implementation-family"].default_enabled is True
    assert (
        RECIPE_PACK_REGISTRY["implementation-family"].description
        == "Implementation and refactoring recipes"
    )


def test_recipe_pack_registry_research_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "research-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["research-family"].default_enabled is False
    assert (
        RECIPE_PACK_REGISTRY["research-family"].description == "Research and exploration recipes"
    )


def test_recipe_pack_registry_orchestration_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "orchestration-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["orchestration-family"].default_enabled is True
    assert (
        RECIPE_PACK_REGISTRY["orchestration-family"].description
        == "Campaign orchestration and automation"
    )


def test_recipe_pack_def_importable_from_core() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY, RecipePackDef

    assert isinstance(RECIPE_PACK_REGISTRY, dict)
    assert all(isinstance(v, RecipePackDef) for v in RECIPE_PACK_REGISTRY.values())

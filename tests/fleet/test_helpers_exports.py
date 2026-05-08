"""Tests that shared helpers are importable from tests.fleet._helpers."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_make_recipe_info_exported_from_fleet_helpers():
    from tests.fleet._helpers import _make_recipe_info

    info = _make_recipe_info("my-recipe")
    assert info.name == "my-recipe"
    assert str(info.path) == "/fake/my-recipe.yaml"


def test_make_recipe_info_custom_prefix():
    from tests.fleet._helpers import _make_recipe_info

    info = _make_recipe_info("my-recipe", path_prefix="/fake/recipes/")
    assert str(info.path) == "/fake/recipes/my-recipe.yaml"


def test_setup_dispatch_exported_from_fleet_helpers():
    from tests.fleet._helpers import _setup_dispatch

    assert callable(_setup_dispatch)

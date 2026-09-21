"""Dispatch identity propagation into bundled recipe skill bindings."""

from __future__ import annotations

from uuid import uuid4

import pytest

from autoskillit.config import (
    build_config_authoritative_layer,
    resolve_ingredient_defaults,
)
from autoskillit.core import BoundValueState
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


@pytest.mark.parametrize("fleet_dispatch", [True, False])
def test_dispatch_identity_reaches_pipeline_health_skill_input(
    tmp_path, monkeypatch: pytest.MonkeyPatch, fleet_dispatch: bool
) -> None:
    dispatch_id = uuid4().hex if fleet_dispatch else ""
    if fleet_dispatch:
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", dispatch_id)

    defaults = resolve_ingredient_defaults(tmp_path)
    layer = build_config_authoritative_layer(defaults)
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    projection = bind_recipe(recipe, ingredient_values=layer)

    dispatch_value = next(
        value
        for value in projection.invocations["analyze_pipeline_health"].skill_inputs
        if value.name == "dispatch_id"
    )

    assert layer["is_fleet_dispatch"] == str(fleet_dispatch).lower()
    assert dispatch_value.effective_value == dispatch_id
    assert dispatch_value.state is BoundValueState.PRESENT

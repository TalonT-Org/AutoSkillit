"""Focused contracts for segmented startup and checkpoint delivery."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    FinalizedRecipeProjection,
    FinalizedRecipeSegment,
    RecipeBindingProjection,
)
from autoskillit.server._recipe_delivery import _uses_segmented_startup

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _projection(*, segmented: bool) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection(invocations={}),
        ordered_step_names=("step",),
        entrypoint="step",
        ordered_flow_edges=(),
        delivery_segments=(
            (FinalizedRecipeSegment(name="Initial", ordered_step_names=("step",)),)
            if segmented
            else ()
        ),
    )


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("open_kitchen", True),
        ("open_kitchen_deferred_recall", True),
        ("load_recipe", False),
        ("get_recipe", False),
    ],
)
def test_compact_startup_is_limited_to_open_kitchen_surfaces(
    surface: str,
    expected: bool,
) -> None:
    assert _uses_segmented_startup(surface, _projection(segmented=True)) is expected
    assert _uses_segmented_startup(surface, _projection(segmented=False)) is False

"""Packaging-time recipe delivery fitness invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.server._recipe_delivery import validate_recipe_exemption_fitness
from tests.contracts._delivery_constants import MAX_PAGES_PER_SECTION
from tests.contracts.fixtures.recipes import (
    ALL_DELIVERY_SURFACES,
    BUNDLED_RECIPE_PATHS,
    backend_forces_bounded,
    compile_bounded_page_plan,
    compile_recipe,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda path: path.stem)
def test_bundled_recipe_ordinary_rendered_admits_exemption_with_margin(
    recipe_path: Path,
) -> None:
    validated = 0
    for surface in ALL_DELIVERY_SURFACES:
        definition = RECIPE_DELIVERY_SURFACE_REGISTRY[surface]
        if definition.response_exemption is None:
            continue
        for backend_name in BACKEND_REGISTRY:
            rendered = compile_recipe(recipe_path, surface, backend_name)
            if backend_forces_bounded(backend_name, surface):
                continue
            validate_recipe_exemption_fitness(
                recipe=recipe_path.stem,
                surface=surface,
                backend=backend_name,
                ordinary_rendered=rendered,
                ceiling_bytes=definition.response_exemption.max_utf8_bytes,
            )
            validated += 1
    assert validated > 0


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda path: path.stem)
@pytest.mark.parametrize("backend_name", sorted(BACKEND_REGISTRY), ids=lambda name: name)
def test_bundled_recipe_bounded_path_compiled_capacity_within_budget(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = next(
        surface
        for surface in ALL_DELIVERY_SURFACES
        if backend_forces_bounded(backend_name, surface)
    )
    envelope = compile_bounded_page_plan(
        recipe_path,
        surface,
        backend_name,
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    if envelope.get("delivery_bound_spill") is not True:
        pytest.skip(
            f"{recipe_path.stem}/{backend_name}: resolves inline under stress-test "
            "config — no bounded path to check"
        )
    assert all(
        item["compiled_page_count"] == item["total_parts"] <= MAX_PAGES_PER_SECTION
        for item in envelope["required_sections"]
    )

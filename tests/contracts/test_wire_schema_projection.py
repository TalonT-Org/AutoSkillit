"""Wire-schema contract: finalized_recipe_projection is absent from delivery.

``finalized_recipe_projection`` is a 205KB internal generation artifact with
zero programmatic readers on the wire. It is retained in the persisted
canonical artifact (``payload.json``) for audit, but must never be injected
into ``surface_payload`` — the dict that becomes the actual delivered
response body across every delivery mode (ordinary-inline, attested-inline,
envelope).

This exercises the real ``finalize_recipe_delivery`` path via
``compile_bounded_page_plan`` (not the pre-finalize ``compile_recipe``
helper, whose payload never carried this field to begin with and so cannot
observe its removal).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY
from autoskillit.execution.backends import BACKEND_REGISTRY
from tests.contracts.fixtures.recipes import (
    ALL_DELIVERY_SURFACES,
    BUNDLED_RECIPE_PATHS,
    backend_forces_bounded,
    compile_bounded_page_plan,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_finalized_wire_payload_excludes_projection(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finalize_recipe_delivery wire body must not carry the projection."""
    validated = 0
    for surface in ALL_DELIVERY_SURFACES:
        definition = RECIPE_DELIVERY_SURFACE_REGISTRY[surface]
        if definition.response_exemption is None:
            continue
        for backend_name in BACKEND_REGISTRY:
            if backend_forces_bounded(backend_name, surface):
                continue
            result = compile_bounded_page_plan(
                recipe_path,
                surface,
                backend_name,
                temp_dir=tmp_path,
                monkeypatch=monkeypatch,
            )
            assert "finalized_recipe_projection" not in result, (
                f"finalized_recipe_projection found in finalized wire payload for "
                f"{recipe_path.stem}/{surface}/{backend_name}"
            )
            validated += 1
    assert validated > 0

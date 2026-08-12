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

import json
from pathlib import Path

import pytest

from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY, client_serialized_char_len
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


def test_implementation_wire_reduction_from_projection_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage F size reduction pin: removing finalized_recipe_projection from the
    wire saves approximately 200KB.

    Measured by comparing the persisted artifact (retains the projection) against
    the delivered wire payload (excludes it). The difference must be at least
    150KB (projection is ~205KB; tolerance accounts for JSON-key overhead).
    """
    impl_path = next(p for p in BUNDLED_RECIPE_PATHS if p.stem == "implementation")
    result = compile_bounded_page_plan(
        impl_path,
        "open_kitchen",
        "claude-code",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    wire_size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # Read the persisted artifact (includes the projection)
    payload_paths = list((tmp_path / "recipe-delivery").rglob("payload.json"))
    assert payload_paths, "no persisted recipe artifact found"
    persisted = json.loads(payload_paths[0].read_text(encoding="utf-8"))
    assert "finalized_recipe_projection" in persisted
    persisted_size = len(
        json.dumps(persisted, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

    reduction_bytes = persisted_size - wire_size
    assert reduction_bytes >= 150_000, (
        f"Expected at least 150KB reduction from projection removal, "
        f"got {reduction_bytes:,} bytes (persisted={persisted_size:,}, wire={wire_size:,})"
    )

    # Client-serialized chars: verify the reduction is also visible in the
    # client-measured domain (not just raw bytes).
    wire_rendered = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    persisted_rendered = json.dumps(persisted, ensure_ascii=False, separators=(",", ":"))
    wire_chars = client_serialized_char_len(wire_rendered).value
    persisted_chars = client_serialized_char_len(persisted_rendered).value
    reduction_chars = persisted_chars - wire_chars
    assert reduction_chars >= 150_000, (
        f"Expected at least 150K client-serialized char reduction, "
        f"got {reduction_chars:,} chars (persisted={persisted_chars:,}, wire={wire_chars:,})"
    )


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_persisted_artifact_retains_projection(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted canonical artifact (payload.json) MUST retain the projection.

    Complementary to ``test_finalized_wire_payload_excludes_projection`` above:
    the projection is excluded from the delivered wire body but must still be
    written to the content-addressed artifact on disk for audit.
    """
    compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "claude-code",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    payload_paths = list((tmp_path / "recipe-delivery").rglob("payload.json"))
    assert payload_paths, f"no persisted recipe artifact found for {recipe_path.stem}"
    found_projection = any(
        "finalized_recipe_projection" in json.loads(path.read_text(encoding="utf-8"))
        for path in payload_paths
    )
    assert found_projection, (
        f"finalized_recipe_projection missing from persisted artifact for {recipe_path.stem}"
    )

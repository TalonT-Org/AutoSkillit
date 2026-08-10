"""Bounded recipe manifests remain within their delivery budget."""

import json
from pathlib import Path

import pytest

from tests.contracts._delivery_constants import MAX_ENVELOPE_MANIFEST_BYTES
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda path: path.stem)
def test_serialized_envelope_manifest_fits_size_budget(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "codex",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    assert len(rendered.encode("utf-8")) <= MAX_ENVELOPE_MANIFEST_BYTES

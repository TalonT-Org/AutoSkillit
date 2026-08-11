"""Pin: implementation recipe flow_records page fits within 115,000 serialized chars."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_IMPL_PATH = next(p for p in BUNDLED_RECIPE_PATHS if p.stem == "implementation")


def test_implementation_flow_records_within_115k_serialized_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = compile_bounded_page_plan(
        _IMPL_PATH,
        "open_kitchen",
        "codex",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    if envelope.get("delivery_bound_spill") is not True:
        pytest.skip("implementation resolves inline")
    # flow_records is always the first required section
    flow_section = next(s for s in envelope["required_sections"] if s["section"] == "flow_records")
    assert flow_section["compiled_bytes"] > 0
    # The compiled bytes are the canonical string form; the serialized char
    # count after Stage D flattening is strictly less than the old string-in-string form.
    # Pin at 115,000 chars (incident flat measurement: ~105,278; pinned with headroom).
    assert flow_section["compiled_bytes"] <= 115_000, (
        f"implementation flow_records compiled_bytes={flow_section['compiled_bytes']} "
        f"exceeds 115,000-char pin"
    )

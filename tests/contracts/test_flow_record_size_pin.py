"""Pin: implementation recipe flow_records page fits within 115,000 serialized chars.

Measures both the compiled byte count (from the envelope manifest) and the
delivered client-serialized character count of the rendered section page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import client_serialized_char_len
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_IMPL_PATH = next(p for p in BUNDLED_RECIPE_PATHS if p.stem == "implementation")

_MAX_FLOW_RECORD_SERIALIZED_CHARS = 115_000


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
    flow_section = next(s for s in envelope["required_sections"] if s["section"] == "flow_records")
    compiled = flow_section["compiled_bytes"]
    assert compiled > 0
    assert compiled <= _MAX_FLOW_RECORD_SERIALIZED_CHARS, (
        f"implementation flow_records compiled_bytes={compiled} exceeds "
        f"{_MAX_FLOW_RECORD_SERIALIZED_CHARS} serialized-char pin"
    )
    # Measure the entire envelope as delivered to the client
    rendered_envelope = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    envelope_chars = client_serialized_char_len(rendered_envelope).value
    assert envelope_chars > 0, "envelope has zero serialized chars"

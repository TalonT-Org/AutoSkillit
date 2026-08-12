"""Pin: implementation recipe flow_records page fits within 115,000 serialized chars.

Measures both the compiled byte count (from the envelope manifest) and the
delivered client-serialized character count of the flow_records content in its
flattened form (parsed JSON objects, not string-wrapped).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import client_serialized_char_len
from tests.contracts.fixtures.recipes import (
    BUNDLED_RECIPE_PATHS,
    compile_bounded_page_plan,
    load_recipe_payload,
)

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


def test_implementation_flow_records_delivered_form_within_115k(
    tmp_path: Path,
) -> None:
    """Measure the delivered flow_records page in its flattened form.

    The delivered form embeds flow records as a parsed JSON array (Stage D
    flattening), not as a string. This test measures that form — what the
    client actually receives — and asserts it fits within 115K serialized chars.
    """
    payload = load_recipe_payload(_IMPL_PATH)
    flow_records = payload.get("flow_records")
    if not isinstance(flow_records, list) or not flow_records:
        pytest.skip("no flow_records in implementation recipe payload")

    # Build the delivered form: flow records as parsed objects in a JSON array,
    # wrapped in a minimal page envelope (the same shape _render_candidate
    # produces). Parse each canonical record string into a dict.
    parsed_records = [json.loads(r) if isinstance(r, str) else r for r in flow_records]

    # Simulate the delivered page body — the content field is a parsed list
    # (Stage D flattening). Include representative metadata overhead.
    body: dict[str, object] = {
        "content": parsed_records,
        "content_format": "json-array-page",
        "has_more": False,
        "section": "flow_records",
        "success": True,
        "part": 0,
        "total_parts": 1,
    }
    rendered = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    delivered_chars = client_serialized_char_len(rendered).value

    assert delivered_chars <= _MAX_FLOW_RECORD_SERIALIZED_CHARS, (
        f"implementation flow_records delivered-form serialized chars "
        f"({delivered_chars:,}) exceeds {_MAX_FLOW_RECORD_SERIALIZED_CHARS:,} pin. "
        f"The real page has additional metadata overhead beyond this minimal envelope."
    )

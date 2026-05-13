"""Tests for prompt-extractor capture field name alignment (Group K).

These tests verify that the sentinel example in the generated L3 prompt
uses the same field names that _extract_captures looks up in the payload.
A mismatch here means campaign captures silently fail at runtime.

The 3-leg round-trip tests close the loop between prompt building, Section 8
parsing, and extraction — ensuring any misalignment (prefix bugs, format drift,
fallback asymmetry) is caught immediately.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import CaptureEntrySpec
from autoskillit.core.types import resolve_payload_field

_SENTINEL_FIELD_RE = re.compile(r'"([\w-]+)":\s*"<[\w-]+_value>"')


def _parse_section8_capture_fields(prompt: str) -> set[str]:
    """Extract capture field names from the Section 8 sentinel JSON example.

    Capture fields in the sentinel example follow the pattern:
        "<field_name>": "<field_name_value>"
    This distinguishes them from the fixed fields (success, reason, summary)
    which use different placeholder formats.
    """
    section8 = prompt[prompt.index("--- SECTION 8") :]
    return set(_SENTINEL_FIELD_RE.findall(section8))


_RECIPE = "test-recipe"
_TASK = "implement feature X"
_INGREDIENTS = {"branch": "main", "issue_url": "https://github.com/org/repo/issues/1"}
_MCP_PREFIX = "mcp__autoskillit__"
_DISPATCH_ID = "abc12345deadbeef"
_CAMPAIGN_ID = "camp-001"
_L3_TIMEOUT = 3600

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.small,
    pytest.mark.feature("fleet"),
]


def test_prompt_capture_fields_match_extractor_expectations():
    """The sentinel example in the prompt must use bare field names
    (no 'capture_' prefix), matching what _extract_captures looks up.

    _build_food_truck_prompt tells the LLM to emit capture fields.
    _extract_captures reads bare field names from the payload result dict.
    If the prompt emits 'capture_worktree_path' but the extractor looks for
    'worktree_path', every capture fails silently.
    """
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    capture_arg = {
        "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
        "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
    }

    prompt = _build_food_truck_prompt(
        recipe=_RECIPE,
        task=_TASK,
        ingredients=_INGREDIENTS,
        mcp_prefix=_MCP_PREFIX,
        dispatch_id=_DISPATCH_ID,
        campaign_id=_CAMPAIGN_ID,
        l3_timeout_sec=_L3_TIMEOUT,
        capture=capture_arg,
    )
    section8 = prompt[prompt.index("--- SECTION 8") :]

    for key, entry in capture_arg.items():
        assert f'"{key}"' in section8, (
            f"Expected bare key '{key}' in sentinel example; "
            f"this is what _extract_captures reads from the payload"
        )
        assert entry.from_ in section8
    assert '"success"' in section8
    assert '"reason"' in section8


def test_prompt_capture_fields_do_not_use_capture_prefix():
    """Sentinel JSON example must NOT use 'capture_' prefix on field names.

    _extract_captures looks for bare field names in the payload dict.
    If the prompt instructs the LLM to emit 'capture_worktree_path' but
    _extract_captures reads 'worktree_path', the capture always fails.
    """
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    capture_arg = {
        "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
    }

    prompt = _build_food_truck_prompt(
        recipe=_RECIPE,
        task=_TASK,
        ingredients=_INGREDIENTS,
        mcp_prefix=_MCP_PREFIX,
        dispatch_id=_DISPATCH_ID,
        campaign_id=_CAMPAIGN_ID,
        l3_timeout_sec=_L3_TIMEOUT,
        capture=capture_arg,
    )
    section8 = prompt[prompt.index("--- SECTION 8") :]

    assert '"capture_worktree_path"' not in section8, (
        "Prompt emits 'capture_worktree_path' but _extract_captures reads "
        "'worktree_path'. This mismatch causes all captures to fail."
    )


# ---------------------------------------------------------------------------
# 3-Leg Round-Trip Contract Tests
# ---------------------------------------------------------------------------
# Leg 1: Build the prompt from a CaptureEntrySpec
# Leg 2: Parse Section 8 to discover field names (not hardcoded)
# Leg 3: Build a synthetic payload with those names, feed through extractor
# ---------------------------------------------------------------------------


def _build_prompt(capture_spec: dict[str, CaptureEntrySpec]) -> str:
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    return _build_food_truck_prompt(
        recipe=_RECIPE,
        task=_TASK,
        ingredients=_INGREDIENTS,
        mcp_prefix=_MCP_PREFIX,
        dispatch_id=_DISPATCH_ID,
        campaign_id=_CAMPAIGN_ID,
        l3_timeout_sec=_L3_TIMEOUT,
        capture=capture_spec,
    )


@pytest.mark.parametrize(
    "capture_spec",
    [
        # Single field, underscore name
        {"worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}")},
        # Two fields, mixed names
        {
            "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
            "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
        },
        # Hyphenated field name
        {"worktree-path": CaptureEntrySpec(from_="${{ result.worktree-path }}")},
        # Five fields (large campaign like research recipe)
        {
            "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
            "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
            "report_path": CaptureEntrySpec(from_="${{ result.report_path }}"),
            "branch_name": CaptureEntrySpec(from_="${{ result.branch_name }}"),
            "summary": CaptureEntrySpec(from_="${{ result.summary }}"),
        },
        # Single-char field name
        {"x": CaptureEntrySpec(from_="${{ result.x }}")},
    ],
    ids=["single", "two-fields", "hyphenated", "five-fields", "single-char"],
)
def test_prompt_to_extraction_round_trip_contract(capture_spec):
    """3-leg contract: build prompt -> parse Section 8 -> extract from synthetic payload.

    Leg 1: Call _build_food_truck_prompt with the capture spec.
    Leg 2: Parse Section 8 sentinel JSON example to discover field names.
    Leg 3: Build a synthetic payload using those names, run _extract_captures.

    If the prompt emits field names that differ from what the extractor expects,
    this test fails — catching prefix bugs, renaming bugs, and format drift.
    """
    from autoskillit.fleet._api import _extract_captures

    # Leg 1: Build prompt
    prompt = _build_prompt(capture_spec)

    # Leg 2: Parse Section 8 to discover field names (not hardcoded)
    parsed_fields = _parse_section8_capture_fields(prompt)
    expected_fields = {resolve_payload_field(e) for e in capture_spec.values()} - {None}

    assert parsed_fields == expected_fields, (
        f"Parsed fields {parsed_fields} != expected {expected_fields}. "
        f"The prompt's Section 8 sentinel format has drifted from what "
        f"resolve_payload_field derives."
    )

    # Leg 3: Build synthetic payload and extract
    fixed_fields = {"success", "reason", "summary"}
    capture_only = parsed_fields - fixed_fields
    synthetic_values = {f: f"test_{f}" for f in capture_only}
    synthetic_payload = {
        "success": True,
        "reason": "completed",
        "summary": "done",
        **synthetic_values,
    }
    result = _extract_captures(capture_spec, synthetic_payload)

    assert len(result) == len(capture_spec), (
        f"Expected {len(capture_spec)} captures, got {len(result)}. "
        f"Payload fields: {parsed_fields}, Result: {result}"
    )
    for key, entry in capture_spec.items():
        assert key in result, f"Capture key '{key}' missing from extraction result: {result}"
        field_name = resolve_payload_field(entry)
        if field_name and field_name in synthetic_values:
            assert result[key] == synthetic_values[field_name], (
                f"Capture '{key}' value mismatch: got {result[key]!r}, "
                f"expected {synthetic_values[field_name]!r}"
            )


@pytest.mark.parametrize(
    "value_type,test_value",
    [
        ("string", "hello world"),
        ("url", "https://github.com/org/repo/pull/42"),
        ("path", None),  # sentinel: test body creates a real file via tmp_path
        ("optional_string", ""),
        ("optional_string", "some value"),
    ],
    ids=["string", "url", "path", "optional-empty", "optional-nonempty"],
)
def test_typed_capture_round_trip_contract(value_type, test_value, tmp_path):
    """Typed captures use the same bare field name in prompt and extractor."""
    from autoskillit.fleet._api import _extract_captures

    # Resolve the path test_value now (tmp_path is available at fixture time)
    resolved_value = str(tmp_path / "test_file") if test_value is None else test_value

    capture_spec = {
        "result_field": CaptureEntrySpec(from_="${{ result.result_field }}", value_type=value_type)
    }
    prompt = _build_prompt(capture_spec)

    parsed_fields = _parse_section8_capture_fields(prompt)
    expected_fields = {resolve_payload_field(e) for e in capture_spec.values()} - {None}
    assert parsed_fields == expected_fields

    # Build synthetic payload — path type needs an existing file
    synthetic_payload: dict[str, object] = {
        "success": True,
        "reason": "completed",
        "summary": "done",
        "result_field": resolved_value,
    }
    if test_value is None:
        # Create the real file for path-type validation
        (tmp_path / "test_file").write_text("test content")

    result = _extract_captures(capture_spec, synthetic_payload)
    assert len(result) == 1
    assert "result_field" in result


def test_non_result_template_fallback_asymmetry():
    """When from_ is not a result.* template, prompt uses key, extractor skips.

    This documents a known asymmetry: the prompt builder falls back to the
    capture key name, but _extract_captures silently skips entries where
    resolve_payload_field returns None. A future fix could unify this behavior,
    but the test ensures the current contract is explicit and documented.
    """
    from autoskillit.fleet._api import _extract_captures

    # Build a capture spec with a non-result.* template
    # CaptureEntrySpec.__post_init__ only checks from_ is non-empty string,
    # so ${{ campaign.x }} is accepted (passes __post_init__, fails _RESULT_REF_RE)
    capture_spec = {"campaign_x": CaptureEntrySpec(from_="${{ campaign.x }}")}
    prompt = _build_prompt(capture_spec)

    # The prompt builder falls back to key name when resolve_payload_field returns None
    section8 = prompt[prompt.index("--- SECTION 8") :]
    assert '"campaign_x"' in section8, (
        "Prompt builder should fall back to key name 'campaign_x' when "
        "resolve_payload_field returns None for non-result.* template"
    )

    # But the extractor skips entries where resolve_payload_field returns None
    synthetic_payload = {
        "success": True,
        "reason": "completed",
        "summary": "done",
        "campaign_x": "test_value",
    }
    result = _extract_captures(capture_spec, synthetic_payload)
    # Extractor skips the entry (field_name is None -> continue)
    assert result == {}, (
        "Extractor should skip non-result.* template entries because "
        "resolve_payload_field returns None, leaving result empty"
    )


def test_prompt_capture_section8_has_no_capture_prefix_when_empty():
    """Section 8 must not contain 'capture_' at all when capture is empty/None."""
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    prompt = _build_food_truck_prompt(
        recipe=_RECIPE,
        task=_TASK,
        ingredients=_INGREDIENTS,
        mcp_prefix=_MCP_PREFIX,
        dispatch_id=_DISPATCH_ID,
        campaign_id=_CAMPAIGN_ID,
        l3_timeout_sec=_L3_TIMEOUT,
        capture=None,
    )
    section8 = prompt[prompt.index("--- SECTION 8") :]
    assert "capture_" not in section8

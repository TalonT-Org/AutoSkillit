"""Tests for prompt-extractor capture field name alignment (Group K).

These tests verify that the sentinel example in the generated L3 prompt
uses the same field names that _extract_captures looks up in the payload.
A mismatch here means campaign captures silently fail at runtime.
"""

from __future__ import annotations

import pytest

# Import fixtures from the prompt test module where they live as module-level constants
from tests.cli.test_food_truck_prompt import (
    _CAMPAIGN_ID,
    _DISPATCH_ID,
    _INGREDIENTS,
    _L3_TIMEOUT,
    _MCP_PREFIX,
    _RECIPE,
    _TASK,
)

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.small,
    pytest.mark.feature("capture-sentinel-contract"),
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

    capture_arg = {"worktree_path": "path to worktree", "pr_url": "URL of the PR"}

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

    for key, description in capture_arg.items():
        # The extractor looks for bare names — the prompt must emit them bare
        assert f'"{key}"' in section8, (
            f"Expected bare key '{key}' in sentinel example; "
            f"this is what _extract_captures reads from the payload"
        )
        assert description in section8
    assert '"success"' in section8
    assert '"reason"' in section8


def test_prompt_capture_fields_do_not_use_capture_prefix():
    """Sentinel JSON example must NOT use 'capture_' prefix on field names.

    _extract_captures looks for bare field names in the payload dict.
    If the prompt instructs the LLM to emit 'capture_worktree_path' but
    _extract_captures reads 'worktree_path', the capture always fails.
    """
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    capture_arg = {"worktree_path": "path to worktree"}

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

    # The extractor reads bare names — the prompt must not use capture_ prefix
    assert '"capture_worktree_path"' not in section8, (
        "Prompt emits 'capture_worktree_path' but _extract_captures reads "
        "'worktree_path'. This mismatch causes all captures to fail."
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

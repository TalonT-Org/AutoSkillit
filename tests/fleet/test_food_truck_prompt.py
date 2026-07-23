"""Tests for fleet/_prompts.py: _build_food_truck_prompt behavioral semantics."""

from __future__ import annotations

import pytest

from autoskillit.core._plugin_ids import DIRECT_PREFIX
from autoskillit.fleet._prompts import _build_food_truck_prompt
from tests.contracts._anti_fab_helpers import FABRICATION_GUARD_RE

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_food_truck_prompt_documents_stop_action():
    """L3 food truck prompt must explain how to handle action:stop steps."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert 'action: "stop"' in prompt or "action: stop" in prompt
    assert "TERMINATE" in prompt.upper() or "terminate" in prompt


def test_food_truck_prompt_contains_hook_denial_compliance():
    """L3 food truck prompt must teach the model that ALL hook denials are mandatory."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "HOOK DENIAL" in prompt.upper()


def test_l2_food_truck_retains_run_skill_guidance_without_machine_frontmatter():
    prompt = _build_food_truck_prompt(
        recipe="process-issues",
        task="Process the issue batches",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "run_skill" in prompt
    assert "retry the EXACT same run_skill call" in prompt
    assert "uses_capabilities:" not in prompt
    assert "execution_role:" not in prompt
    assert "backend_requirements:" not in prompt


def test_fleet_prompt_contains_budget_exceeded_routing():
    """L3 food truck prompt must contain QUOTA WAIT REQUIRED and QUOTA BUDGET EXCEEDED routing."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "QUOTA WAIT REQUIRED" in prompt
    assert "QUOTA BUDGET EXCEEDED" in prompt


def test_h3b_stop_step_semantics_references_sentinel_and_success():
    """H3b must instruct sentinel block emission with success field."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    h3b_start = prompt.find("H3b — STOP STEP SEMANTICS:")
    h3c_start = prompt.find("H3c — ROUTE STEP SEMANTICS:")
    assert h3b_start != -1, "H3b STOP STEP SEMANTICS section not found in prompt"
    h3b_section = prompt[h3b_start:h3c_start] if h3c_start != -1 else prompt[h3b_start:]
    assert "sentinel" in h3b_section.lower(), (
        "H3b must reference 'sentinel' for stop step handling"
    )
    assert "success" in h3b_section.lower(), (
        "H3b must instruct setting success field in sentinel block"
    )


def test_fleet_prompt_contains_missing_on_failure_sentinel():
    """The L2 fleet prompt must instruct the model to emit missing_on_failure sentinel."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "recipe authoring error. Emit the sentinel block with success=false" in prompt
    assert 'reason="missing_on_failure"' in prompt


_FABRICATION_GUARD_RE = FABRICATION_GUARD_RE


def test_food_truck_prompt_has_anti_fabrication_guard():
    """L3 food truck prompt must include anti-fabrication language."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert _FABRICATION_GUARD_RE.search(prompt), (
        "Food truck prompt must include anti-fabrication language"
    )
    assert "ROUTING AUTHORITY" in prompt


def test_food_truck_prompt_injects_caller_instructions_section():
    """L3 food truck prompt must inject caller instructions when provided."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
        caller_instructions="use opus for implement",
    )
    assert "CALLER INSTRUCTIONS" in prompt
    assert "use opus for implement" in prompt
    assert "SECTION 6b" in prompt


def test_food_truck_prompt_no_caller_instructions_section_when_none():
    """Food truck prompt must omit caller instructions section when caller_instructions is None."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "CALLER INSTRUCTIONS" not in prompt


def test_food_truck_prompt_no_caller_instructions_section_when_empty():
    """Food truck prompt omits CALLER INSTRUCTIONS when caller_instructions is empty."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
        caller_instructions="",
    )
    assert "CALLER INSTRUCTIONS" not in prompt


def test_food_truck_open_kitchen_predicate_uses_json_field_not_substring():
    """Food truck predicate must use JSON-field check, not substring marker."""
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    idx = prompt.index("FAILURE PREDICATE — open_kitchen")
    section = prompt[idx : idx + 500]
    assert "--- INGREDIENTS TABLE ---" not in section
    assert "ingredients_table" in section


def test_food_truck_prompt_has_degraded_response_halt_rule():
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    assert "FAILURE PREDICATE — DEGRADED RESPONSE" in prompt
    assert 'reason="degraded_tool_response"' in prompt


def test_food_truck_prompt_degraded_rule_triggers_sentinel_on_empty_content():
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    start = prompt.index("FAILURE PREDICATE — DEGRADED RESPONSE")
    end = prompt.index("TWO FAILURE TIERS", start)
    section = prompt[start:end]
    assert "success" in section
    assert "false" in section
    assert any(word in section for word in ("absent", "null", "empty")), (
        "Rule must reference absent, null, or empty content"
    )


def test_food_truck_prompt_degraded_reason_in_reason_enum():
    prompt = _build_food_truck_prompt(
        recipe="test-recipe",
        task="Test task",
        ingredients={},
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    fields_idx = prompt.index("Fields:", prompt.index("--- SECTION 8:"))
    reason_region = prompt[fields_idx : fields_idx + 300]
    assert "degraded_tool_response" in reason_region, (
        "degraded_tool_response must appear in Section 8 reason enum"
    )
    ok_idx = prompt.index("FAILURE PREDICATE — open_kitchen")
    deg_idx = prompt.index("FAILURE PREDICATE — DEGRADED RESPONSE")
    assert ok_idx != deg_idx, "Degraded and open_kitchen predicates must be distinct blocks"

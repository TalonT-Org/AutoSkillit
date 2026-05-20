"""Contract: fleet dispatcher prompt contains BEM pre-step gate instructions."""

from __future__ import annotations

import pytest

from autoskillit.cli._prompts_kitchen import _build_fleet_dispatch_prompt

MCP_PREFIX = "autoskillit__"


@pytest.fixture(scope="module")
def fleet_prompt() -> str:
    return _build_fleet_dispatch_prompt(mcp_prefix=MCP_PREFIX)


def test_fleet_prompt_contains_bem_wrapper_reference(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must reference bem-wrapper for conflict analysis."""
    assert "bem-wrapper" in fleet_prompt, (
        "Fleet dispatcher prompt must instruct the dispatcher to use bem-wrapper "
        "as the BEM pre-step for multi-issue dispatches"
    )


def test_fleet_prompt_contains_dispatch_plan_reference(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must reference dispatch_plan from l3_payload."""
    assert "dispatch_plan" in fleet_prompt, (
        "Fleet dispatcher prompt must instruct the dispatcher to read dispatch_plan "
        "from the bem-wrapper dispatch_food_truck response"
    )


def test_fleet_prompt_contains_multi_issue_gate_language(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must contain multi-issue gate language."""
    lower = fleet_prompt.lower()
    assert any(phrase in lower for phrase in ("multi-issue", "2 or more", "2+ issues")), (
        "Fleet dispatcher prompt must describe the multi-issue BEM gate trigger condition"
    )


def test_fleet_prompt_does_not_contain_stale_serial_guidance(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must NOT contain stale serial-only dispatch guidance."""
    assert "dispatch one food truck at a time" not in fleet_prompt, (
        "Stale 'Serial execution: dispatch one food truck at a time' text must be "
        "replaced by BEM-gated parallel dispatch guidance (REQ-BEM-002)"
    )


def test_fleet_prompt_references_fallback_to_sequential(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must describe sequential fallback when BEM fails."""
    lower = fleet_prompt.lower()
    assert "fallback" in lower or "fall back" in lower or "sequential" in lower, (
        "Fleet dispatcher prompt must describe the sequential fallback path when "
        "bem-wrapper fails or returns an empty dispatch_plan"
    )


def test_fleet_prompt_references_max_total_issues_cap(fleet_prompt: str) -> None:
    """Fleet dispatcher prompt must describe the max_total_issues cap."""
    assert "max_total_issues" in fleet_prompt, (
        "Fleet dispatcher prompt must mention the total issues session cap"
    )


def test_fleet_prompt_bem_gate_uses_mandatory_heading(fleet_prompt: str) -> None:
    """BEM gate heading must include MANDATORY to match sous-chef standard."""
    bem_to_discipline = fleet_prompt.split("## DISPATCHER DISCIPLINE")[0]
    assert "MANDATORY" in bem_to_discipline, (
        "BEM pre-step gate heading must include 'MANDATORY' — weak headings "
        "allow the LLM to treat the gate as advisory documentation"
    )


def test_fleet_prompt_bem_gate_uses_must_directive(fleet_prompt: str) -> None:
    """BEM gate must contain MUST directives for enforcement."""
    parts = fleet_prompt.split("## MULTI-ISSUE")
    assert len(parts) >= 2, "Fleet prompt must contain ## MULTI-ISSUE heading"
    bem_section = parts[1].split("## DISPATCHER DISCIPLINE")[0]
    assert "MUST" in bem_section.upper(), (
        "BEM gate section must contain at least one MUST directive — "
        "soft imperative language is insufficient for LLM compliance"
    )


def test_fleet_prompt_bem_gate_has_never_block(fleet_prompt: str) -> None:
    """BEM gate must contain NEVER prohibitions to prevent bypass."""
    parts = fleet_prompt.split("## MULTI-ISSUE")
    assert len(parts) >= 2, "Fleet prompt must contain ## MULTI-ISSUE heading"
    bem_section = parts[1].split("## DISPATCHER DISCIPLINE")[0]
    assert "NEVER" in bem_section, (
        "BEM gate section must contain NEVER directives prohibiting "
        "multi-issue dispatch without prior BEM execution"
    )

"""Tests for orchestrator prompt contract: failure predicates and dispatch consistency."""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _get_prompt() -> str:
    """Return the orchestrator prompt for a demo recipe."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    return _build_orchestrator_prompt("demo", "mcp__autoskillit__")


class TestOpenKitchenFailurePredicate:
    """Guards for the FAILURE PREDICATE — open_kitchen block in the orchestrator prompt."""

    def test_prompt_contains_open_kitchen_failure_predicate(self):
        prompt = _get_prompt()
        assert "FAILURE PREDICATE — open_kitchen" in prompt

    def test_prompt_open_kitchen_predicate_mentions_success_false_substring(self):
        prompt = _get_prompt()
        # Find the open_kitchen predicate section
        idx = prompt.index("FAILURE PREDICATE — open_kitchen")
        section = prompt[idx : idx + 500]
        assert '"success": false' in section

    def test_prompt_open_kitchen_predicate_mentions_user_visible_message(self):
        prompt = _get_prompt()
        idx = prompt.index("FAILURE PREDICATE — open_kitchen")
        section = prompt[idx : idx + 500]
        assert "user_visible_message" in section

    def test_prompt_open_kitchen_predicate_forbids_askuserquestion(self):
        prompt = _get_prompt()
        idx = prompt.index("FAILURE PREDICATE — open_kitchen")
        section = prompt[idx : idx + 500]
        assert "DO NOT call AskUserQuestion" in section

    def test_open_kitchen_predicate_uses_substring_not_json_field_dispatch(self):
        """Negative: the predicate block must NOT use JSON-field dispatch phrasing."""
        prompt = _get_prompt()
        idx = prompt.index("FAILURE PREDICATE — open_kitchen")
        section = prompt[idx : idx + 500]
        assert "json.loads" not in section.lower()
        assert "parsed[" not in section


class TestStep0ToolPredicateCoverage:
    """Every tool referenced in STEP 0 must have a failure predicate or shared rule."""

    def test_every_step0_tool_has_failure_predicate_or_shared_rule(self):
        """Parse STEP 0 section, extract tool names, assert each has a predicate."""
        prompt = _get_prompt()

        # Extract tool names from STEP 0 (tools appear as {mcp_prefix}<tool> or explicit names)
        step0_match = re.search(
            r"FIRST ACTION.*?(?=ROUTING RULES|FAILURE PREDICATES|During pipeline)",
            prompt,
            re.DOTALL,
        )
        assert step0_match is not None, "STEP 0 / FIRST ACTION section not found"
        step0_text = step0_match.group()

        # Find tool names: mcp__autoskillit__<tool>(<args>)
        tool_names = set(re.findall(r"mcp__autoskillit__(\w+)\(", step0_text))
        assert len(tool_names) > 0, "No tool names found in STEP 0"

        # Each tool must appear in a FAILURE PREDICATE section
        for tool in tool_names:
            assert f"FAILURE PREDICATE — {tool}" in prompt or f"- {tool}:" in prompt, (
                f"Tool '{tool}' in STEP 0 has no failure predicate or shared rule"
            )


class TestFirstActionAskUserQuestionProhibition:
    """FIRST ACTION section must explicitly prohibit AskUserQuestion before open_kitchen."""

    def test_first_action_prohibits_ask_user_question_before_open_kitchen(self):
        """The FIRST ACTION section must contain an explicit prohibition on
        AskUserQuestion before open_kitchen."""
        prompt = _get_prompt()
        first_action_start = prompt.index("FIRST ACTION")
        # Find the end of the FIRST ACTION section (next major section)
        first_action_end = prompt.index("During pipeline execution", first_action_start)
        first_action_section = prompt[first_action_start:first_action_end]
        assert "DO NOT call AskUserQuestion" in first_action_section


class TestFirstActionTimingResilience:
    """FIRST ACTION step 0 must be a ToolSearch readiness check before open_kitchen."""

    def test_first_action_step0_is_toolsearch_gate(self):
        """Step 0 in FIRST ACTION must be a ToolSearch readiness check — not a bare Bash sleep."""
        prompt = _get_prompt()
        first_action_start = prompt.index("FIRST ACTION")
        first_action_end = prompt.index("During pipeline execution", first_action_start)
        first_action_section = prompt[first_action_start:first_action_end]

        # Step 0 must be ToolSearch
        step0_end = first_action_section.index("\n1.")
        step0 = first_action_section[:step0_end]
        assert "ToolSearch" in step0, "Step 0 must be a ToolSearch readiness check"
        assert "open_kitchen" in step0, "Step 0 ToolSearch must target open_kitchen"

        # Step 0 must not open with a Bash call
        assert not step0.lstrip().startswith("Bash"), (
            "Step 0 must not begin with Bash — ToolSearch is the first instruction"
        )

        # ToolSearch must still appear in FIRST ACTION
        assert "ToolSearch" in first_action_section

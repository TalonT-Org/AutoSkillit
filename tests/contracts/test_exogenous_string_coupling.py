"""Exogenous string coupling tests.

These tests assert that every string referenced in orchestrator prompts as a trigger
or routing condition is coupled to the module that actually emits it. If either side
drifts, these tests fail immediately — preventing silent recovery-path breakage.
"""

from __future__ import annotations

import re


def test_quota_guard_deny_trigger_coupled_to_prompt():
    """QUOTA_GUARD_DENY_TRIGGER constant in quota_guard must appear verbatim
    in the orchestrator prompt's QUOTA DENIAL ROUTING section."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt
    from autoskillit.hooks.quota_guard import QUOTA_GUARD_DENY_TRIGGER

    prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
    assert QUOTA_GUARD_DENY_TRIGGER in prompt, (
        f"Prompt must reference quota_guard.QUOTA_GUARD_DENY_TRIGGER "
        f"({QUOTA_GUARD_DENY_TRIGGER!r}) verbatim"
    )


def test_quota_post_warning_trigger_coupled_to_prompt():
    """QUOTA_POST_WARNING_TRIGGER constant in quota_post_hook must appear verbatim
    in the orchestrator prompt's QUOTA DENIAL ROUTING section."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt
    from autoskillit.hooks.quota_post_hook import QUOTA_POST_WARNING_TRIGGER

    prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
    assert QUOTA_POST_WARNING_TRIGGER in prompt, (
        f"Prompt must reference quota_post_hook.QUOTA_POST_WARNING_TRIGGER "
        f"({QUOTA_POST_WARNING_TRIGGER!r}) verbatim"
    )


def test_quota_post_warning_trigger_coupled_to_sous_chef_skill():
    """QUOTA_POST_WARNING_TRIGGER constant must also appear in sous-chef SKILL.md
    so both prompt surfaces stay in sync with the hook emit string."""
    from autoskillit.core import pkg_root
    from autoskillit.hooks.quota_post_hook import QUOTA_POST_WARNING_TRIGGER

    skill_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    assert skill_path.exists(), "sous-chef SKILL.md not found"
    content = skill_path.read_text()
    assert QUOTA_POST_WARNING_TRIGGER in content, (
        f"sous-chef SKILL.md must reference quota_post_hook.QUOTA_POST_WARNING_TRIGGER "
        f"({QUOTA_POST_WARNING_TRIGGER!r}) verbatim"
    )


class TestPromptToolReachability:
    """Tools referenced in FIRST ACTION must be registered in the FastMCP app."""

    def test_prompt_tool_reachability(self):
        """Each MCP tool name in FIRST ACTION must exist in the FastMCP tool registry."""
        from autoskillit.cli._prompts import _build_orchestrator_prompt
        from autoskillit.core.types._type_constants import FREE_RANGE_TOOLS, GATED_TOOLS

        registered_tools = {*GATED_TOOLS, *FREE_RANGE_TOOLS}

        prompt = _build_orchestrator_prompt("test", "mcp__autoskillit__")
        fa_start = prompt.index("FIRST ACTION")
        fa_end = prompt.find("During pipeline execution", fa_start)
        assert fa_end != -1, "'During pipeline execution' section not found after FIRST ACTION"
        first_action = prompt[fa_start:fa_end]

        tool_names = set(re.findall(r"mcp__autoskillit__(\w+)\(", first_action))
        assert len(tool_names) > 0, "No MCP tool names found in FIRST ACTION"

        unregistered = [t for t in tool_names if t not in registered_tools]
        assert unregistered == [], (
            f"FIRST ACTION references tool(s) not in the FastMCP registry: {unregistered}. "
            f"Either add them to GATED_TOOLS/FREE_RANGE_TOOLS or remove from the prompt."
        )


class TestPromptToolsWhitelistCoupling:
    """FIRST ACTION must not reference native tools blocked by --tools AskUserQuestion."""

    def test_first_action_references_no_blocked_native_tools(self):
        """FIRST ACTION must not mention any PIPELINE_FORBIDDEN_TOOLS by name."""
        from autoskillit.cli._mcp_names import DIRECT_PREFIX
        from autoskillit.cli._prompts import _build_orchestrator_prompt
        from autoskillit.core.types._type_constants import PIPELINE_FORBIDDEN_TOOLS

        prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
        start = prompt.index("FIRST ACTION")
        end = prompt.find("During pipeline execution", start)
        assert end != -1, "'During pipeline execution' section not found after FIRST ACTION"
        first_action = prompt[start:end]

        found = [t for t in PIPELINE_FORBIDDEN_TOOLS if t in first_action]
        assert found == [], (
            f"FIRST ACTION references forbidden native tools {found} that are blocked "
            f"by --tools AskUserQuestion in _session_launch.py"
        )

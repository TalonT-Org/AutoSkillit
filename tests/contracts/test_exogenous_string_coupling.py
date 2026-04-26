"""Exogenous string coupling tests.

These tests assert that every string referenced in orchestrator prompts as a trigger
or routing condition is coupled to the module that actually emits it. If either side
drifts, these tests fail immediately — preventing silent recovery-path breakage.
"""

from __future__ import annotations


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


class TestPromptToolsWhitelistCoupling:
    """FIRST ACTION must not reference native tools blocked by --tools AskUserQuestion."""

    def test_first_action_references_no_blocked_native_tools(self):
        """Extract CamelCase native tool names from FIRST ACTION; assert none are blocked."""
        import re

        from autoskillit.cli._mcp_names import DIRECT_PREFIX
        from autoskillit.cli._prompts import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
        start = prompt.index("FIRST ACTION")
        end = prompt.index("During pipeline execution", start)
        first_action = prompt[start:end]

        native_tools = re.findall(r"[Cc]all ([A-Z][a-zA-Z]+)\(", first_action)
        assert native_tools == [], (
            f"FIRST ACTION references native tools {native_tools} that are blocked "
            f"by --tools AskUserQuestion in _session_launch.py"
        )

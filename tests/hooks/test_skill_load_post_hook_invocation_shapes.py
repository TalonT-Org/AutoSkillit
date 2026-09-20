"""Invocation-shape discrimination for skill binding hooks."""

from __future__ import annotations

import pytest

from autoskillit.hooks.skill_load_post_hook import _invocation_skill

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_user_prompt_expansion_extracts_slash_command() -> None:
    assert _invocation_skill(
        {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "autoskillit:rectify",
        }
    ) == ("UserPromptExpansion", "autoskillit:rectify")


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "UserPromptExpansion", "expansion_type": "mcp_prompt"},
        {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
    ],
)
def test_non_skill_invocations_are_ignored(payload) -> None:
    assert _invocation_skill(payload) is None

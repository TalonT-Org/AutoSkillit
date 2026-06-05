"""Tests for _claude_prompt backend prompt utilities."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends._claude_prompt import _ensure_skill_prefix

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_skill_prefix_no_raw_fallback():
    result = _ensure_skill_prefix("/foo args", provider_profile="openai")
    assert "read the skill instructions from the skill's SKILL.md file" not in result
    assert "Skill tool" in result


def test_compose_resume_prompt_does_not_duplicate_open_kitchen():
    from autoskillit.execution.backends._claude_prompt import _compose_resume_prompt

    base_prompt = (
        "FIRST ACTION: Your first action should be to load the skill instructions "
        'by calling the Skill tool with skill="sous-chef". '
        "Then call open_kitchen to start the session.\n\n"
        "TASK: Fix the bug in module X."
    )
    result = _compose_resume_prompt(base_prompt=base_prompt, resume_checkpoint=None)

    assert result.count("open_kitchen") <= 1
    assert "do NOT call open_kitchen again" in result


def test_compose_resume_prompt_strips_first_action_block():
    from autoskillit.execution.backends._claude_prompt import _compose_resume_prompt

    base_prompt = (
        "FIRST ACTION: Call open_kitchen with recipe=fix-bugs.\n\nTASK: Implement the feature."
    )
    result = _compose_resume_prompt(base_prompt=base_prompt, resume_checkpoint=None)

    assert "FIRST ACTION" not in result
    assert "TASK: Implement the feature." in result

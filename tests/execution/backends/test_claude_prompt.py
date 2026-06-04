"""Tests for _claude_prompt backend prompt utilities."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends._claude_prompt import _ensure_skill_prefix

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_skill_prefix_no_raw_fallback():
    result = _ensure_skill_prefix("/foo args", provider_profile="openai")
    assert "read the skill instructions from the skill's SKILL.md file" not in result
    assert "Skill tool" in result

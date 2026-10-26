"""Contracts for skills that discover Claude project JSONL logs."""

from __future__ import annotations

import pytest

from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_LOG_DIR_COMMAND = 'LOG_DIR=$(autoskillit claude-project-log-dir "$actual_absolute_cwd")'
_SKILL_SECTIONS = (
    ("investigate", "#### Part A: Mine Past Investigation Logs", "#### Part B:"),
    ("audit-friction", "### Step 1: Locate Project Logs", "### Step 3:"),
    ("audit-bugs", "### Step 1: Locate Project Logs", "### Step 3:"),
    ("setup-project", "**Subagent F+ — Conversation History Mining", "### Step 2:"),
)


def _section(skill_name: str, start_marker: str, end_marker: str) -> str:
    skill = DefaultSkillResolver().resolve(skill_name)
    assert skill is not None
    text = skill.path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


@pytest.mark.parametrize(("skill_name", "start_marker", "end_marker"), _SKILL_SECTIONS)
def test_claude_log_mining_uses_native_project_log_resolution(
    skill_name: str, start_marker: str, end_marker: str
) -> None:
    section = _section(skill_name, start_marker, end_marker)
    cwd_index = section.find("actual_absolute_cwd=")
    command_index = section.find(_LOG_DIR_COMMAND)
    discovery_index = section.find('"$LOG_DIR"', command_index)

    assert 0 <= cwd_index < command_index < discovery_index
    assert "//\\//-" not in section
    assert 'LOG_DIR="${LOG_DIR//--/-}"' not in section

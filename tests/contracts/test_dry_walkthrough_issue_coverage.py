"""The walkthrough distinguishes verified plan sets from standalone issue checks."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/skills_extended/dry-walkthrough/SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _coverage_section(text: str) -> str:
    return text.split("### Step 4.6: Plan-vs-Issue Coverage Check", 1)[1].split(
        "### Step 4.7:", 1
    )[0]


def _mode(section: str, letter: str, next_letter: str | None = None) -> str:
    start = section.index(f"**{letter}.")
    end = section.index(f"**{next_letter}.", start) if next_letter else len(section)
    return section[start:end]


def test_four_modes_are_ordered_and_exclusive(skill_text: str) -> None:
    section = _coverage_section(skill_text)
    assert "Exactly one mode applies" in section
    assert [section.index(f"**{letter}.") for letter in "ABCD"] == sorted(
        section.index(f"**{letter}.") for letter in "ABCD"
    )
    assert skill_text.index("### Step 4.5") < skill_text.index("### Step 4.6")
    assert skill_text.index("### Step 4.6") < skill_text.index("### Step 4.7")


def test_authority_mode_checks_only_assigned_requirements(skill_text: str) -> None:
    mode = re.sub(r"\s+", " ", _mode(_coverage_section(skill_text), "A", "B"))
    assert "verified_plan_set_preflight" in mode
    assert "assigned_requirements" in mode
    assert "Do not fetch the issue" in mode
    assert "do not read, open, or reference any other part" in mode
    assert "do not stamp" in mode
    assert "aggregate coverage" in mode


def test_standalone_single_part_mode_remains_blocking(skill_text: str) -> None:
    mode = _mode(_coverage_section(skill_text), "B", "C")
    assert "gh issue view" in mode
    assert "Enumerate" in mode
    assert "block stamping" in mode


def test_standalone_multipart_warns_without_whole_issue_verdict(skill_text: str) -> None:
    mode = _mode(_coverage_section(skill_text), "C", "D")
    assert "Plan-set coverage not verified" in mode
    assert "do not evaluate whole-issue coverage" in mode
    assert "do not block" in mode


def test_no_issue_mode_and_authority_never_rule(skill_text: str) -> None:
    mode = _mode(_coverage_section(skill_text), "D")
    assert "No issue context" in mode
    assert "omitted" in mode
    assert re.search(
        r"\*\*NEVER:\*\*[\s\S]*?Open plan-set authority artifacts directly",
        skill_text,
    )
    assert re.search(
        r"If the plan filename contains `_part_`",
        skill_text,
    )


def test_arguments_document_issue_and_authority(skill_text: str) -> None:
    arguments = skill_text.split("## Arguments", 1)[1].split("## Critical Constraints", 1)[0]
    assert "issue_url" in arguments
    assert "plan_set_authority_path" in arguments

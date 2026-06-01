"""Cross-skill contract tests for source-attribution directives."""

from __future__ import annotations

from pathlib import Path

import pytest
import regex as re

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

_SOURCE_ATTRIBUTION_PATTERN = re.compile(
    r"(?:NOT|NEVER|DO NOT)[\s\S]{0,200}?"
    r"(?:issue\s+title|issue\s+body|issue\s+metadata|closing_issue|"
    r"re-?deriv|overrid|substitut|branch\s+names|ambient\s+context)"
    r"[\s\S]{0,200}?"
    r"(?:task_title|title|## Title)",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "skill_name",
    ["prepare-pr", "compose-pr"],
)
def test_skill_has_source_attribution_prohibition(skill_name: str) -> None:
    """Skills with dual-source patterns must contain explicit source-attribution prohibition."""
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    assert skill_md.exists(), f"{skill_name}/SKILL.md not found"
    text = skill_md.read_text()
    assert _SOURCE_ATTRIBUTION_PATTERN.search(text), (
        f"{skill_name}/SKILL.md must contain a proximity-anchored prohibition against "
        f"using prohibited sources (issue metadata, branch names) for task_title derivation"
    )

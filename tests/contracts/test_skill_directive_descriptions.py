"""SKILL.md directive description contract: headless recipe skills use directive language."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "src" / "autoskillit" / "skills_extended"

_FM_PATTERN = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

_HEADLESS_RECIPE_SKILLS = [
    "implement-worktree-no-merge",
    "implement-worktree",
    "retry-worktree",
    "dry-walkthrough",
    "make-plan",
    "resolve-failures",
    "compose-pr",
    "prepare-pr",
    "diagnose-ci",
]


def _get_description(skill_name: str) -> str:
    skill_md = _SKILLS_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    m = _FM_PATTERN.match(content)
    assert m, f"{skill_name}/SKILL.md must have YAML frontmatter"
    for line in m.group(1).splitlines():
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    pytest.fail(f"{skill_name}/SKILL.md frontmatter missing description field")


@pytest.mark.parametrize("skill_name", _HEADLESS_RECIPE_SKILLS)
def test_headless_recipe_skills_use_directive_descriptions(skill_name: str) -> None:
    """T4-1: Each headless recipe skill description uses directive language."""
    desc = _get_description(skill_name)

    assert re.match(r"^[A-Z][a-z]", desc), (
        f"{skill_name}: description must start with a capitalized noun phrase, got: {desc!r}"
    )
    assert "ALWAYS invoke this skill" in desc, (
        f"{skill_name}: description must contain 'ALWAYS invoke this skill', got: {desc!r}"
    )
    assert "Do not" in desc, (
        f"{skill_name}: description must contain 'Do not' prohibition, got: {desc!r}"
    )

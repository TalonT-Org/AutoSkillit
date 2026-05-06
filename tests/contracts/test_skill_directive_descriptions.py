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


def _get_diagnose_ci_content() -> str:
    skill_md = _SKILLS_DIR / "diagnose-ci" / "SKILL.md"
    return skill_md.read_text()


def test_diagnose_ci_step_numbering_is_sequential() -> None:
    """T2: diagnose-ci steps are sequential starting from 1 with no gaps."""
    content = _get_diagnose_ci_content()
    step_headings = re.findall(r"^### Step (\d+):", content, re.MULTILINE)
    step_numbers = [int(n) for n in step_headings]
    expected = list(range(1, len(step_numbers) + 1))
    assert step_numbers == expected, (
        f"diagnose-ci step numbers must be sequential 1..{len(step_numbers)}, got {step_numbers}"
    )


def test_diagnose_ci_step_crossref_resolves_correctly() -> None:
    """T3: diagnose-ci 'proceed to Step N' cross-reference points to correct step."""
    content = _get_diagnose_ci_content()
    # Find the cross-reference: "proceed to Step N (..."
    m = re.search(r"proceed to Step (\d+)\s+\(([^)]+)\)", content)
    assert m, "Could not find 'proceed to Step N (...)' cross-reference in diagnose-ci/SKILL.md"
    step_num = int(m.group(1))
    parenthetical = m.group(2).lower()
    # Find the heading for that step
    heading_pattern = rf"^### Step {step_num}:\s+(.+)$"
    heading_m = re.search(heading_pattern, content, re.MULTILINE)
    assert heading_m, f"Step {step_num} heading not found in diagnose-ci/SKILL.md"
    heading_title = heading_m.group(1).lower()
    # The title should contain both words from the parenthetical
    for word in parenthetical.split():
        assert word in heading_title, (
            f"Cross-ref 'proceed to Step {step_num} ({parenthetical})' points to "
            f"'### Step {step_num}: {heading_m.group(1)}' but '{word}' not found in title"
        )

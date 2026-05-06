"""diagnose-ci SKILL.md step-structure contracts: numbering and cross-reference integrity."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "src" / "autoskillit" / "skills_extended"


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
    """T3: every 'proceed to Step N (...)' cross-reference in diagnose-ci resolves correctly."""
    content = _get_diagnose_ci_content()
    matches = re.findall(r"proceed to Step (\d+)\s+\(([^)]+)\)", content)
    assert matches, (
        "Could not find any 'proceed to Step N (...)' cross-reference in diagnose-ci/SKILL.md"
    )
    for step_num_str, parenthetical in matches:
        step_num = int(step_num_str)
        parenthetical_lower = parenthetical.lower()
        heading_pattern = rf"^### Step {step_num}:\s+(.+)$"
        heading_m = re.search(heading_pattern, content, re.MULTILINE)
        assert heading_m, f"Step {step_num} heading not found in diagnose-ci/SKILL.md"
        heading_title = heading_m.group(1).lower()
        assert parenthetical_lower in heading_title, (
            f"Cross-ref 'proceed to Step {step_num} ({parenthetical})' points to "
            f"'### Step {step_num}: {heading_m.group(1)}' — "
            f"'{parenthetical}' not found as a phrase in title"
        )

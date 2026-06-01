"""Generic intra-skill variable threading contracts.

Verifies that variables computed in one step of a SKILL.md are actually used
in the correct downstream commands, not replaced by hardcoded values.

Adding new contracts is a one-line addition to THREADING_CONTRACTS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_git_commands,
    extract_step_sections,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILLS_DIRS = [
    _REPO_ROOT / "src" / "autoskillit" / "skills",
    _REPO_ROOT / "src" / "autoskillit" / "skills_extended",
]


def _find_skill_md(skill_name: str) -> Path:
    for d in _SKILLS_DIRS:
        candidate = d / skill_name / "SKILL.md"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"SKILL.md not found for skill '{skill_name}'")


@dataclass(frozen=True)
class ThreadingContract:
    skill: str
    source_step: str
    variable: str
    target_step: str
    must_appear_in_pattern: str
    must_not_hardcode: str | None = None


THREADING_CONTRACTS: list[ThreadingContract] = [
    ThreadingContract(
        skill="audit-impl",
        source_step="Step 0",
        variable="implementation_ref",
        target_step="Step 2",
        must_appear_in_pattern=r"git (diff|log)\b",
        must_not_hardcode="HEAD",
    ),
]


@pytest.mark.parametrize(
    "contract",
    THREADING_CONTRACTS,
    ids=lambda c: f"{c.skill}:{c.variable}",
)
def test_variable_threading(contract: ThreadingContract) -> None:
    """Variables computed in one step must appear in the correct downstream commands."""
    skill_md = _find_skill_md(contract.skill)
    content = skill_md.read_text(encoding="utf-8")
    sections = extract_step_sections(content)

    assert contract.source_step in sections, (
        f"{contract.skill} SKILL.md must contain {contract.source_step!r}"
    )
    assert contract.target_step in sections, (
        f"{contract.skill} SKILL.md must contain {contract.target_step!r}"
    )

    source_text = sections[contract.source_step]
    assert contract.variable in source_text, (
        f"{contract.skill} {contract.source_step} must mention '{contract.variable}' "
        "(variable must be computed/resolved in the source step)"
    )

    target_text = sections[contract.target_step]
    target_cmds = extract_git_commands(target_text)
    pattern = re.compile(contract.must_appear_in_pattern)
    matching_cmds = [c for c in target_cmds if pattern.search(c)]

    assert matching_cmds, (
        f"{contract.skill} {contract.target_step} must contain commands matching "
        f"'{contract.must_appear_in_pattern}'"
    )

    placeholder = "{" + contract.variable + "}"
    ref_present = any(placeholder in c for c in matching_cmds)
    assert ref_present, (
        f"{contract.skill} {contract.target_step} commands matching "
        f"'{contract.must_appear_in_pattern}' must reference '{{{contract.variable}}}'. "
        f"Commands found: {matching_cmds}"
    )

    if contract.must_not_hardcode:
        hardcoded = [
            c
            for c in matching_cmds
            if contract.must_not_hardcode in c and "rev-parse" not in c and "abbrev-ref" not in c
        ]
        assert not hardcoded, (
            f"{contract.skill} {contract.target_step} commands must not hardcode "
            f"'{contract.must_not_hardcode}'; use '{{{contract.variable}}}' instead. "
            f"Offending commands: {hardcoded}"
        )

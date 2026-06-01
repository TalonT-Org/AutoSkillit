"""Guards for resolve-review SKILL.md: blind git add, /tmp scratch-file."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root

SKILL_MD = pkg_root() / "skills_extended" / "resolve-review" / "SKILL.md"

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def test_resolve_review_no_blind_add() -> None:
    """resolve-review/SKILL.md must not contain any blind git add form."""
    text = SKILL_MD.read_text()
    assert "add -A" not in text, "resolve-review/SKILL.md still contains 'add -A'"
    assert "add --all" not in text, "resolve-review/SKILL.md still contains 'add --all'"
    for line in text.splitlines():
        stripped = line.strip()
        if "git add ." in stripped and "add -- " not in stripped:
            raise AssertionError(
                f"resolve-review/SKILL.md contains blind 'git add .': {stripped!r}"
            )


def test_never_constraint_mentions_tmp() -> None:
    """NEVER constraint must explicitly prohibit /tmp scratch files."""
    content = SKILL_MD.read_text()
    never_section = re.search(
        r"\*\*NEVER:\*\*\s*\n(.*?)(?:\n\*\*ALWAYS:\*\*|\n##|\Z)",
        content,
        re.DOTALL,
    )
    assert never_section is not None, "NEVER section not found in SKILL.md"
    never_text = never_section.group(1)
    assert "/tmp" in never_text, (
        "NEVER constraint must explicitly mention /tmp to prevent scratch-file writes"
    )

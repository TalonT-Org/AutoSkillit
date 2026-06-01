"""Guards for generate-report SKILL.md: blind git add prevention."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_MD = pkg_root() / "skills_extended" / "generate-report" / "SKILL.md"


def test_generate_report_no_blind_add() -> None:
    """generate-report/SKILL.md must not contain any blind git add form."""
    text = SKILL_MD.read_text()
    assert "add -A" not in text, "generate-report/SKILL.md still contains 'add -A'"
    assert "add --all" not in text, "generate-report/SKILL.md still contains 'add --all'"
    for line in text.splitlines():
        stripped = line.strip()
        if "git add ." in stripped and "add -- " not in stripped:
            raise AssertionError(
                f"generate-report/SKILL.md contains blind 'git add .': {stripped!r}"
            )

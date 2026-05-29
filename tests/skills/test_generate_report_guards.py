"""Guards for generate-report SKILL.md: blind git add prevention."""

from __future__ import annotations

from autoskillit.core.paths import pkg_root

SKILL_MD = pkg_root() / "skills_extended" / "generate-report" / "SKILL.md"


def test_generate_report_no_blind_add() -> None:
    """generate-report/SKILL.md must not contain blind 'git add -A'."""
    text = SKILL_MD.read_text()
    assert "add -A" not in text, "generate-report/SKILL.md still contains 'add -A'"

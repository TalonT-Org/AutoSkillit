"""Guards for resolve-review SKILL.md: blind git add prevention."""

from __future__ import annotations

from autoskillit.core.paths import pkg_root

SKILL_MD = pkg_root() / "skills_extended" / "resolve-review" / "SKILL.md"


def test_resolve_review_no_blind_add() -> None:
    """resolve-review/SKILL.md must not contain blind 'git add -A'."""
    text = SKILL_MD.read_text()
    assert "add -A" not in text, "resolve-review/SKILL.md still contains 'add -A'"

"""Tier 2/3 SKILL.md files must use ``{{AUTOSKILLIT_TEMP}}``, never the literal."""

from __future__ import annotations

from pathlib import Path

_LITERAL = ".autoskillit/temp"
_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"


def _tier23_root() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "skills_extended"


def test_tier2_3_skill_md_has_no_literal_temp_path() -> None:
    root = _tier23_root()
    assert root.is_dir(), f"Tier 2/3 skills root not found: {root}"

    offenders: list[str] = []
    for skill_md in root.rglob("SKILL.md"):
        if _LITERAL in skill_md.read_text(encoding="utf-8"):
            offenders.append(skill_md.relative_to(root).as_posix())
    assert not offenders, (
        f"Tier 2/3 SKILL.md files contain literal {_LITERAL!r} (must use "
        f"{_PLACEHOLDER!r} instead): {offenders}"
    )


def test_tier2_3_at_least_one_skill_uses_placeholder() -> None:
    """Sanity check that the migration ran — at least 50 SKILL.md files contain the placeholder."""
    root = _tier23_root()
    count = sum(
        1
        for skill_md in root.rglob("SKILL.md")
        if _PLACEHOLDER in skill_md.read_text(encoding="utf-8")
    )
    assert count >= 50, (
        f"Expected ≥50 Tier 2/3 SKILL.md files to use {_PLACEHOLDER!r}; found {count}"
    )

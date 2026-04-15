"""Tier 1 SKILL.md files must not reference temp at all.

Tier 1 skills (open-kitchen, close-kitchen, sous-chef) bypass the ephemeral
copy pipeline and the placeholder substitution. Any temp reference in their
SKILL.md is unsubstituted at runtime, so neither the literal path nor the
``{{AUTOSKILLIT_TEMP}}`` placeholder is permitted.
"""

from __future__ import annotations

from pathlib import Path

_LITERAL = ".autoskillit/temp"
_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"


def _tier1_root() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "skills"


def test_tier1_skill_md_has_no_temp_reference() -> None:
    root = _tier1_root()
    assert root.is_dir(), f"Tier 1 skills root not found: {root}"

    offenders: list[str] = []
    for skill_md in root.rglob("SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        if _LITERAL in text or _PLACEHOLDER in text:
            offenders.append(skill_md.relative_to(root).as_posix())
    assert not offenders, (
        "Tier 1 SKILL.md files must contain neither the literal "
        f"{_LITERAL!r} nor {_PLACEHOLDER!r}: {offenders}"
    )

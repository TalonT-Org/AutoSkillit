"""Skill contract: exploration-vector markers require preflight documentation
(#4684 Fix F / 2.8).

Before this contract, no SKILL.md or agent definition mentioned
``enable_exploration`` at all —
``rg -rln "enable_exploration" src/autoskillit/skills/ src/autoskillit/skills_extended/
src/autoskillit/agents/`` returned zero files. A skill author who adds an
``exploration-vector`` marker shipped with no instruction that a preflight call
is required. This test enumerates every SKILL.md containing the marker (mirrors
the enumeration shape of tests/contracts/test_explorer_conformance_preamble.py)
and asserts each carries a structured preflight block: a Markdown blockquote of
the form ``> **Preflight:** ...`` with ``enable_exploration`` appearing within
200 characters of the anchor. A loose, unrelated mention of
``enable_exploration`` elsewhere in the file does not satisfy the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_MARKER_RE = re.compile(r'<!--\s*autoskillit:exploration-vector\s+id="')
_PREFLIGHT_ANCHOR_RE = re.compile(r">\s*\*\*Preflight:\*\*", re.IGNORECASE)
_PREFLIGHT_WINDOW = 200


def _skill_md_files_with_exploration_vector_marker() -> list[Path]:
    roots = [pkg_root() / "skills", pkg_root() / "skills_extended"]
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            if _MARKER_RE.search(path.read_text(encoding="utf-8")):
                found.append(path)
    return found


_MARKED_SKILLS = _skill_md_files_with_exploration_vector_marker()


def _skill_id(path: Path) -> str:
    return str(path.relative_to(pkg_root()))


@pytest.mark.parametrize("skill_path", _MARKED_SKILLS, ids=[_skill_id(p) for p in _MARKED_SKILLS])
def test_exploration_vector_skill_has_preflight_block(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    anchor = _PREFLIGHT_ANCHOR_RE.search(text)
    assert anchor is not None, (
        f"{_skill_id(skill_path)} has an exploration-vector marker but no "
        "'> **Preflight:**' block. Add one instructing the session to call "
        "enable_exploration before acting on the exploration-vector directives."
    )
    window = text[anchor.end() : anchor.end() + _PREFLIGHT_WINDOW]
    assert "enable_exploration" in window, (
        f"{_skill_id(skill_path)}'s preflight block must mention enable_exploration "
        f"within {_PREFLIGHT_WINDOW} characters of the '> **Preflight:**' anchor — a "
        "loose mention elsewhere in the file does not satisfy the contract."
    )


def test_discovery_walk_finds_the_known_marked_skills() -> None:
    """Sanity: the enumeration walk itself must not silently find nothing."""
    assert len(_MARKED_SKILLS) >= 1, (
        "No SKILL.md with an exploration-vector marker discovered — "
        "the enumeration walk may be broken."
    )

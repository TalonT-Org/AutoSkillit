"""Tests that SKILL.md files containing git commit instructions prohibit --amend usage.

Encodes a prohibition assertion: any skill that instructs an agent to run
``git commit`` must also explicitly forbid ``--amend`` or require new commits.
This catches future skills that introduce commit instructions without prohibition.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]


def _all_skill_dirs() -> list[Path]:
    dirs = []
    for skills_dir in _SKILLS_DIRS:
        if skills_dir.exists():
            dirs.extend(
                d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
            )
    return sorted(dirs, key=lambda d: d.name)


_SKILL_DIRS = _all_skill_dirs()

_AMEND_PROHIBITION_RE = re.compile(
    r"(?i)(?:do\s+not|never|don't)\s+.{0,30}--amend|--amend.{0,30}(?:do\s+not|never|prohibited)",
)
_NEW_COMMIT_RE = re.compile(
    r"(?i)(?:new|fresh)\s+commit|always\s+create\s+new\s+commits",
)


_GIT_COMMIT_INSTRUCTION_RE = re.compile(
    r"git\s+(?:-C\s+\S+\s+)?commit\s+-m",
)


@pytest.mark.parametrize("skill_dir", _SKILL_DIRS, ids=lambda d: d.name)
def test_skill_commit_prohibits_amend(skill_dir: Path) -> None:
    """Skills with 'git commit -m' instructions must explicitly prohibit --amend or require
    new commits.  Skills with no commit instructions (or only descriptive mentions without
    -m flag) are considered low-risk and pass."""
    text = (skill_dir / "SKILL.md").read_text()

    if not _GIT_COMMIT_INSTRUCTION_RE.search(text):
        return

    has_prohibition = bool(_AMEND_PROHIBITION_RE.search(text))
    has_new_commit_language = bool(_NEW_COMMIT_RE.search(text))

    assert has_prohibition or has_new_commit_language, (
        f"Skill {skill_dir.name!r} contains 'git commit' instructions but does not "
        "explicitly prohibit '--amend' or require new commits. "
        "Add 'do NOT use --amend' or 'always create new commits' near each commit instruction."
    )

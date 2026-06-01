"""Structural guard: SKILL.md files must not use single-brace {AUTOSKILLIT_TEMP}.

{{AUTOSKILLIT_TEMP}} (double braces) is the correct template placeholder.
Single-brace {AUTOSKILLIT_TEMP} is a typo that leaves the variable unreplaced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_SKILLS_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "skills_extended"

# Matches {AUTOSKILLIT_TEMP} without the surrounding double-brace escaping.
# Negative lookbehind excludes {{ and negative lookahead excludes }}.
_SINGLE_BRACE_RE = re.compile(r"(?<!\{)\{AUTOSKILLIT_TEMP\}(?!\})")


def test_no_single_brace_autoskillit_temp() -> None:
    """No SKILL.md file may use {AUTOSKILLIT_TEMP} (single brace).

    The correct form is {{AUTOSKILLIT_TEMP}} (double brace). Single-brace
    occurrences indicate a copy-paste error where the template variable was
    never substituted correctly.
    """
    violations: list[str] = []
    for skill_dir in sorted(_SKILLS_ROOT.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text(encoding="utf-8")
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if _SINGLE_BRACE_RE.search(line):
                violations.append(f"{skill_dir.name}/SKILL.md:{lineno}: {line.strip()}")

    assert not violations, (
        "Single-brace {AUTOSKILLIT_TEMP} found in SKILL.md files "
        "(should be {{AUTOSKILLIT_TEMP}}):\n" + "\n".join(f"  {v}" for v in violations)
    )

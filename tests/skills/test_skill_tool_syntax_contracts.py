"""Validates that SKILL.md bash blocks do not contain grep BRE \\| alternation patterns
that would be silently broken if copied to the Grep tool's pattern parameter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import extract_bash_blocks
from autoskillit.recipe.rules.rules_skill_content import _GIT_GREP_BRE_RE, _GREP_BRE_ALTERNATION_RE

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILL_DIRS = [
    _REPO_ROOT / "src" / "autoskillit" / "skills",
    _REPO_ROOT / "src" / "autoskillit" / "skills_extended",
]


def _all_skill_dirs() -> list[Path]:
    result = []
    for skill_dir in _SKILL_DIRS:
        if not skill_dir.exists():
            continue
        result.extend(p for p in sorted(skill_dir.iterdir()) if p.is_dir())
    return result


def _is_git_grep_bre(line: str) -> bool:
    """Return True if the \\| is inside a --grep= argument (git BRE context — allowed)."""
    return bool(_GIT_GREP_BRE_RE.search(line))


@pytest.mark.parametrize("skill_dir", _all_skill_dirs())
def test_no_grep_bre_alternation_in_bash_blocks(skill_dir: Path) -> None:
    """No SKILL.md bash block should contain grep '...\\|...' patterns.

    grep uses POSIX BRE where \\| is alternation, but the native Grep tool wraps
    ripgrep which uses ERE where | (bare) is alternation. Models that copy \\| from
    bash blocks into Grep tool pattern arguments get 0 results silently.

    Use rg 'foo|bar' in bash blocks (ripgrep syntax, identical to Grep tool).
    Exception: --grep= arguments in git log/show commands are legitimate BRE.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return
    bash_blocks = extract_bash_blocks(skill_md.read_text())
    violations: list[str] = []
    for block in bash_blocks:
        for line in block.splitlines():
            if _is_git_grep_bre(line):
                continue  # git --grep= BRE context: allowed
            if _GREP_BRE_ALTERNATION_RE.search(line):
                violations.append(f"  Line: {line.strip()!r}")
    assert not violations, (
        f"{skill_md.relative_to(Path.cwd())} contains grep BRE \\| patterns in bash "
        f"blocks that could confuse models using the Grep tool:\n"
        + "\n".join(violations)
        + "\n\nFix: replace `grep 'foo\\|bar'` with `rg 'foo|bar'` "
        "(ripgrep ERE matches Grep tool syntax)."
    )

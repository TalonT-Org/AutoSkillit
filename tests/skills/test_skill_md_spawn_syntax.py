"""Regression guard: SKILL.md subagent spawn instructions must use unambiguous syntax."""

from __future__ import annotations

import re
from pathlib import Path

_AMBIGUOUS_PATTERNS = [
    # "(Task tool, model: ..." or "(Task tool, `model:" parenthetical
    re.compile(r"\(\s*Task\s+tool\s*,\s*`?model:"),
    # "Task subagent(s) (" — wrong tool name as subagent label
    re.compile(r"\bTask\s+subagents?\s*\("),
    # Standalone "(model: sonnet)" or '(model: "sonnet")' or '(`model: "sonnet"`)' —
    # no Agent context
    re.compile(r'\(\s*`?model:\s*"?sonnet"?\s*`?\s*\)'),
    # "Task tool subagent" phrasing
    re.compile(r"\bTask\s+tool\s+subagent"),
]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILL_DIRS = [
    _REPO_ROOT / "src" / "autoskillit" / "skills",
    _REPO_ROOT / "src" / "autoskillit" / "skills_extended",
    _REPO_ROOT / ".claude" / "skills",
]


def _collect_violations() -> list[str]:
    violations: list[str] = []
    for d in _SKILL_DIRS:
        if not d.exists():
            continue
        for md in sorted(d.rglob("SKILL.md")):
            for i, line in enumerate(md.read_text().splitlines(), 1):
                for pat in _AMBIGUOUS_PATTERNS:
                    if pat.search(line):
                        rel = md.relative_to(_REPO_ROOT)
                        violations.append(f"  {rel}:{i}: {line.strip()}")
                        break
    return violations


def test_no_ambiguous_spawn_model_parameter() -> None:
    """SKILL.md files must not use ambiguous (Task tool, model: sonnet) patterns.

    See: https://github.com/TalonT-Org/AutoSkillit/issues/3367
    """
    violations = _collect_violations()
    assert not violations, "Ambiguous subagent spawn patterns found (issue #3367):\n" + "\n".join(
        violations
    )

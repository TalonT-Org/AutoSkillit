"""Ensure all skills using Claude-Code-only features declare backend_requirements."""

from __future__ import annotations

import pytest

from autoskillit.core import paths
from autoskillit.workspace.skills import _read_skill_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CLAUDE_CODE_ONLY_PATTERNS: tuple[str, ...] = (
    "Agent(model=",
    "Agent(subagent_type=",
    "open_kitchen",
    "close_kitchen",
    "run_skill",
    "test_check",
    "autoskillit:",
    ".claude/",
)


def test_claude_code_skills_have_backend_requirements():
    """Skills using Claude-Code-only features must declare backend_requirements: [claude-code]."""
    pkg = paths.pkg_root()
    violations: list[str] = []

    for skill_dir in (pkg / "skills", pkg / "skills_extended"):
        if not skill_dir.is_dir():
            continue
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            content = skill_md.read_text(encoding="utf-8")
            uses_claude_code_feature = any(
                pattern in content for pattern in _CLAUDE_CODE_ONLY_PATTERNS
            )
            if not uses_claude_code_feature:
                continue
            fm = _read_skill_frontmatter(skill_md)
            reqs = fm.get("backend_requirements", [])
            if "claude-code" not in reqs:
                violations.append(entry.name)

    assert not violations, (
        f"{len(violations)} skill(s) use Claude-Code-only features but lack "
        f"backend_requirements: [claude-code]:\n" + "\n".join(f"  {v}" for v in violations)
    )

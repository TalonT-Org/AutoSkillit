"""Reverse-direction annotation validation: annotated skills must justify their annotation."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import paths
from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace.skills import _read_skill_frontmatter
from tests.arch._helpers import _strip_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_GENUINE_CLAUDE_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Agent\(\s*subagent_type\s*="),
    re.compile(r"Agent\(\s*model\s*="),
    re.compile(r"\bopen_kitchen\b"),
    re.compile(r"\bclose_kitchen\b"),
    re.compile(r"\brun_skill\b"),
    re.compile(r"\btest_check\b"),
)


def _has_genuine_claude_code_dependency(body: str, skill_name: str) -> bool:
    for pat in _GENUINE_CLAUDE_CODE_PATTERNS:
        if pat.search(body):
            return True
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        if "autoskillit:" in stripped:
            if f"autoskillit:{skill_name}" in stripped:
                continue
            cap = SKILL_CAPABILITY_REGISTRY.get("cross_skill_ref")
            if cap and cap.required_backends:
                return True
    return False


def test_annotated_skills_have_justified_annotation():
    pkg = paths.pkg_root()
    over_annotated: list[str] = []

    for skill_dir in (pkg / "skills", pkg / "skills_extended"):
        if not skill_dir.is_dir():
            continue
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm = _read_skill_frontmatter(skill_md)
            reqs = fm.get("backend_requirements", [])
            if "claude-code" not in reqs:
                continue
            content = skill_md.read_text(encoding="utf-8")
            body = _strip_frontmatter(content)
            if not _has_genuine_claude_code_dependency(body, entry.name):
                over_annotated.append(entry.name)

    assert not over_annotated, (
        f"{len(over_annotated)} skill(s) have backend_requirements: [claude-code] "
        f"without genuine justification:\n" + "\n".join(f"  {s}" for s in over_annotated)
    )

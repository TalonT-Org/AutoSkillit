"""Contracts: SKILL.md activate_deps must cover invoked Skill tool calls.

Catches the four known cases (rectify, open-integration-pr, elaborate-phase,
make-arch-diag) and prevents future regressions where a SKILL.md body invokes
``/autoskillit:<name>`` via the Skill tool without declaring the corresponding
``activate_deps`` entry.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.workspace.session_skills import (
    SkillsDirectoryProvider,
    compute_skill_closure,
)
from autoskillit.workspace.skills import bundled_skills_extended_dir

_INVOCATION_PATTERNS = (
    re.compile(
        r"(?i)\bLOAD(?:ED)?\b[^\n]{0,120}/autoskillit:([a-z][\w-]+)[^\n]{0,120}\bSkill tool\b"
    ),
    re.compile(r"(?i)\bSkill tool\b[^\n]{0,120}/autoskillit:([a-z][\w-]+)"),
)

_FM_PATTERN = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)


def _strip_frontmatter(content: str) -> str:
    m = _FM_PATTERN.match(content)
    return m.group(2) if m else content


@pytest.fixture(scope="module")
def provider() -> SkillsDirectoryProvider:
    return SkillsDirectoryProvider()


def test_make_arch_diag_declares_arch_lens_dep(provider: SkillsDirectoryProvider) -> None:
    closure = compute_skill_closure("make-arch-diag", provider)
    assert "make-arch-diag" in closure
    assert any(name.startswith("arch-lens-") for name in closure), closure


def test_elaborate_phase_declares_dry_walkthrough_dep(provider: SkillsDirectoryProvider) -> None:
    closure = compute_skill_closure("elaborate-phase", provider)
    assert "dry-walkthrough" in closure, closure


def test_rectify_declares_arch_lens_dep(provider: SkillsDirectoryProvider) -> None:
    closure = compute_skill_closure("rectify", provider)
    assert any(name.startswith("arch-lens-") for name in closure), closure


def test_open_integration_pr_declares_arch_lens_dep(provider: SkillsDirectoryProvider) -> None:
    closure = compute_skill_closure("open-integration-pr", provider)
    assert any(name.startswith("arch-lens-") for name in closure), closure


def test_all_skills_skill_tool_invocations_match_activate_deps(
    provider: SkillsDirectoryProvider,
) -> None:
    """Every Skill-tool invocation must resolve to a name in the invoker's closure.

    Captured names that do not resolve to a real skill (template placeholders such
    as ``arch-lens-*`` or ``exp-lens-{slug}``) are silently skipped.
    """
    extended_dir = bundled_skills_extended_dir()
    failures: list[str] = []
    for skill_md in sorted(extended_dir.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        body = _strip_frontmatter(skill_md.read_text())
        invoked: set[str] = set()
        for pattern in _INVOCATION_PATTERNS:
            for match in pattern.finditer(body):
                invoked.add(match.group(1))
        real_invoked = {n for n in invoked if provider.resolver.resolve(n) is not None}
        if not real_invoked:
            continue
        closure = compute_skill_closure(skill_name, provider)
        for name in sorted(real_invoked):
            if name not in closure:
                failures.append(
                    f"{skill_md.parent.name}/SKILL.md: invokes /autoskillit:{name} "
                    f"but it is not in compute_skill_closure({skill_name!r})"
                )
    assert not failures, "Undeclared Skill-tool invocations:\n" + "\n".join(failures)

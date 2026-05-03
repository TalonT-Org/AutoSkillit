"""Contracts: SKILL.md activate_deps must cover invoked Skill tool calls.

Catches the four known cases (rectify, open-integration-pr, elaborate-phase,
make-arch-diag) and prevents future regressions where a SKILL.md body invokes
``/autoskillit:<name>`` via the Skill tool without declaring the corresponding
``activate_deps`` entry.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants import PACK_REGISTRY, SKILL_ACTIVATE_DEPS_REQUIRED
from autoskillit.workspace.session_skills import (
    SkillsDirectoryProvider,
    compute_skill_closure,
)
from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir

_INVOCATION_PATTERNS = (
    re.compile(
        r"(?i)\bLOAD(?:ED)?\b[^\n]{0,120}/autoskillit:([a-z][\w-]+)[^\n]{0,120}\bSkill tool\b"
    ),
    re.compile(r"(?i)\bSkill tool\b[^\n]{0,120}/autoskillit:([a-z][\w-]+)"),
)

_FM_PATTERN = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)
_ACTIVATE_DEPS_LINE_RE = re.compile(r"^activate_deps:\s*\[([^\]]*)\]", re.MULTILINE)


def _strip_frontmatter(content: str) -> str:
    m = _FM_PATTERN.match(content)
    return m.group(2) if m else content


def _get_activate_deps(skill_md: Path) -> list[str]:
    """Parse activate_deps from a SKILL.md frontmatter (bracket list format)."""
    content = skill_md.read_text()
    m = _FM_PATTERN.match(content)
    frontmatter = m.group(1) if m else content
    deps_m = _ACTIVATE_DEPS_LINE_RE.search(frontmatter)
    if not deps_m:
        return []
    return [d.strip() for d in deps_m.group(1).split(",") if d.strip()]


def _find_skill_md(skill_name: str) -> Path | None:
    for base in (bundled_skills_dir(), bundled_skills_extended_dir()):
        candidate = base / skill_name / "SKILL.md"
        if candidate.exists():
            return candidate
    return None


@pytest.fixture(scope="module")
def provider() -> SkillsDirectoryProvider:
    return SkillsDirectoryProvider()


def test_make_arch_diag_declares_arch_lens_dep(provider: SkillsDirectoryProvider) -> None:
    closure = compute_skill_closure("make-arch-diag", provider)
    assert "make-arch-diag" in closure  # contract: target is always included in its own closure
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


def test_required_activate_deps_present() -> None:
    """For each (skill, required_deps) in SKILL_ACTIVATE_DEPS_REQUIRED, the skill's
    SKILL.md must declare all required deps in activate_deps."""
    failures: list[str] = []
    for skill_name, required_deps in SKILL_ACTIVATE_DEPS_REQUIRED.items():
        skill_md = _find_skill_md(skill_name)
        if skill_md is None:
            failures.append(f"{skill_name}: SKILL.md not found")
            continue
        declared = set(_get_activate_deps(skill_md))
        for dep in sorted(required_deps):
            if dep not in declared:
                failures.append(
                    f"{skill_name}/SKILL.md: SKILL_ACTIVATE_DEPS_REQUIRED requires "
                    f"{dep!r} in activate_deps, but only {sorted(declared)!r} declared"
                )
    assert not failures, "Missing required activate_deps:\n" + "\n".join(failures)


def test_all_activate_deps_resolve() -> None:
    """Every name in any SKILL.md's activate_deps must resolve to a real skill or pack.

    No silent drops — a typo in activate_deps is caught at CI time.
    """
    pack_keys = set(PACK_REGISTRY.keys())
    resolver_cache: dict[str, bool] = {}

    # Use a fresh provider resolver to check skill resolution
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()

    def _resolves(name: str) -> bool:
        if name not in resolver_cache:
            resolver_cache[name] = name in pack_keys or resolver.resolve(name) is not None
        return resolver_cache[name]

    failures: list[str] = []
    for base in (bundled_skills_dir(), bundled_skills_extended_dir()):
        for skill_md in sorted(base.glob("*/SKILL.md")):
            for dep in _get_activate_deps(skill_md):
                if not _resolves(dep):
                    failures.append(
                        f"{skill_md.parent.name}/SKILL.md: activate_deps contains "
                        f"{dep!r} which does not resolve to a skill or pack"
                    )
    assert not failures, "Unresolvable activate_deps entries:\n" + "\n".join(failures)

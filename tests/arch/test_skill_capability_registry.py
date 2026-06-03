"""Capability registry consistency: every capability is consumed and derivable."""

from __future__ import annotations

import pytest

from autoskillit.core import paths
from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace.skills import _read_skill_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _all_skill_frontmatter() -> list[tuple[str, dict]]:
    pkg = paths.pkg_root()
    results: list[tuple[str, dict]] = []
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
            results.append((entry.name, fm))
    return results


@pytest.mark.xfail(reason="registry not yet consumed — rectify plan in progress")
def test_all_capability_keys_are_consumed():
    all_fm = _all_skill_frontmatter()
    used_caps: set[str] = set()
    for _name, fm in all_fm:
        for cap in fm.get("uses_capabilities", []):
            used_caps.add(cap)
    unused = set(SKILL_CAPABILITY_REGISTRY) - used_caps
    assert not unused, (
        f"Capability keys not referenced by any SKILL.md uses_capabilities: {sorted(unused)}"
    )


@pytest.mark.xfail(reason="co-requirement not yet enforced — rectify plan in progress")
def test_backend_requirements_derivable_from_capabilities():
    all_fm = _all_skill_frontmatter()
    violations: list[str] = []
    for name, fm in all_fm:
        backend_reqs = frozenset(fm.get("backend_requirements", []))
        uses_caps = list(fm.get("uses_capabilities", []))
        if backend_reqs and not uses_caps:
            violations.append(f"{name}: has backend_requirements but no uses_capabilities")
            continue
        if not uses_caps:
            continue
        derived: set[str] = set()
        for cap_name in uses_caps:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(cap_name)
            if cap_def:
                derived |= cap_def.required_backends
        if frozenset(derived) != backend_reqs:
            violations.append(
                f"{name}: derived={sorted(derived)}, declared={sorted(backend_reqs)}"
            )
    assert not violations, (
        f"{len(violations)} skill(s) have inconsistent backend_requirements vs "
        f"uses_capabilities:\n" + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.xfail(reason="uses_capabilities not yet declared — rectify plan in progress")
def test_no_unknown_capability_declared():
    all_fm = _all_skill_frontmatter()
    violations: list[str] = []
    for name, fm in all_fm:
        for cap in fm.get("uses_capabilities", []):
            if cap not in SKILL_CAPABILITY_REGISTRY:
                violations.append(f"{name}: unknown capability '{cap}'")
    assert not violations, "Unknown capabilities declared in SKILL.md files:\n" + "\n".join(
        f"  {v}" for v in violations
    )

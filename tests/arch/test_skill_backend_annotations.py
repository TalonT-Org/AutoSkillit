"""Bidirectional backend annotation enforcement with capability-aware detection.

Forward: skill content using a capability → uses_capabilities must declare it.
Reverse: backend_requirements present → at least one capability justifies it.
Co-requirement: backend_requirements non-empty → uses_capabilities must also exist.
Derivation: backend_requirements == union of required_backends from uses_capabilities.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace.skills import _read_skill_frontmatter
from tests.arch._helpers import _iter_skill_dirs, _strip_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CAPABILITY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "agent_subagent": [re.compile(r"Agent\(\s*subagent_type\s*=")],
    "agent_model": [re.compile(r"Agent\(\s*model\s*=")],
    "open_kitchen": [re.compile(r"\bopen_kitchen\b"), re.compile(r"\bclose_kitchen\b")],
    "run_skill": [re.compile(r"\brun_skill\b")],
    "test_check": [re.compile(r"\btest_check\b")],
    "claude_dir": [re.compile(r"\.claude/")],
}


def _detect_capabilities(body: str, skill_name: str) -> set[str]:
    detected: set[str] = set()
    for cap_name, patterns in _CAPABILITY_PATTERNS.items():
        for pat in patterns:
            if pat.search(body):
                detected.add(cap_name)
                break
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "autoskillit:" in stripped:
            if f"autoskillit:{skill_name}" not in stripped:
                detected.add("cross_skill_ref")
                break
    return detected


def test_forward_check_capabilities_declared():
    """Skills using a capability must declare it in uses_capabilities."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        content = skill_md.read_text(encoding="utf-8")
        body = _strip_frontmatter(content)
        detected = _detect_capabilities(body, name)
        if not detected:
            continue
        fm = _read_skill_frontmatter(skill_md)
        declared = set(fm.get("uses_capabilities", []))
        missing = detected - declared
        if missing:
            violations.append(f"{name}: detected={sorted(detected)}, missing={sorted(missing)}")
    assert not violations, (
        f"{len(violations)} skill(s) use capabilities but don't declare them:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_reverse_check_annotation_justified():
    """Skills with backend_requirements must have at least one capability justifying it."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        backend_reqs = set(fm.get("backend_requirements", []))
        if not backend_reqs:
            continue
        uses_caps = list(fm.get("uses_capabilities", []))
        if not uses_caps:
            violations.append(f"{name}: has backend_requirements but no uses_capabilities")
            continue
        derived: set[str] = set()
        for cap_name in uses_caps:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(cap_name)
            if cap_def:
                derived |= cap_def.required_backends
        for req in backend_reqs:
            if req not in derived:
                violations.append(
                    f"{name}: backend_requirements includes '{req}' "
                    f"but no declared capability requires it"
                )
    assert not violations, (
        f"{len(violations)} annotation(s) not justified by capabilities:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_co_requirement_backend_requires_capabilities():
    """Non-empty backend_requirements → uses_capabilities must also be declared."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        if fm.get("backend_requirements") and not fm.get("uses_capabilities"):
            violations.append(name)
    assert not violations, (
        f"{len(violations)} skill(s) have backend_requirements without uses_capabilities:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_derivation_backend_requirements_match_capabilities():
    """backend_requirements must equal the union of required_backends from uses_capabilities."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        uses_caps = list(fm.get("uses_capabilities", []))
        if not uses_caps:
            continue
        derived: set[str] = set()
        for cap_name in uses_caps:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(cap_name)
            if cap_def:
                derived |= cap_def.required_backends
        declared = set(fm.get("backend_requirements", []))
        if derived != declared:
            violations.append(f"{name}: derived={sorted(derived)}, declared={sorted(declared)}")
    assert not violations, (
        f"{len(violations)} skill(s) have mismatched backend_requirements vs capabilities:\n"
        + "\n".join(f"  {v}" for v in violations)
    )

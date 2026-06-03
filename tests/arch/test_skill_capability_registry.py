"""Capability registry consistency: every capability is consumed and derivable."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace.skills import _read_skill_frontmatter
from tests.arch._helpers import _iter_skill_dirs

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _all_skill_frontmatter() -> list[tuple[str, dict]]:
    return [(name, _read_skill_frontmatter(skill_md)) for name, skill_md in _iter_skill_dirs()]


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


def test_codex_status_consistent_with_required_backends():
    from autoskillit.core.types._type_constants_env import AGENT_BACKEND_CLAUDE_CODE

    violations = []
    for key, cap in SKILL_CAPABILITY_REGISTRY.items():
        if cap.codex_status != "not-applicable" and cap.required_backends == frozenset(
            {AGENT_BACKEND_CLAUDE_CODE}
        ):
            violations.append(
                f"{key}: codex_status={cap.codex_status!r} contradicts required_backends"
            )
    assert not violations, "\n".join(f"  {v}" for v in violations)


_MCP_TOOL_CAPABILITIES = {"run_skill", "test_check", "open_kitchen"}


def test_mcp_tools_are_backend_agnostic():
    violations = []
    for cap_name in _MCP_TOOL_CAPABILITIES:
        cap = SKILL_CAPABILITY_REGISTRY.get(cap_name)
        if cap and cap.required_backends:
            violations.append(f"{cap_name}: required_backends={cap.required_backends!r}")
    assert not violations, "MCP tools must have empty required_backends:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_required_backends_is_derived_property():
    from autoskillit.core.types._type_constants_registries import SkillCapabilityDef

    assert "required_backends" not in SkillCapabilityDef.__dataclass_fields__

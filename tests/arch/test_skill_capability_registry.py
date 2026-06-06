"""Capability registry consistency: every capability is consumed and derivable."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.core.types._type_enums import SessionType
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
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    violations: list[str] = []
    for skill_info in resolver.list_all():
        uses_caps = list(skill_info.uses_capabilities)
        if not uses_caps:
            if skill_info.backend_requirements:
                violations.append(
                    f"{skill_info.name}: has backend_requirements but no uses_capabilities"
                )
            continue
        derived: set[str] = set()
        for cap_name in uses_caps:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(cap_name)
            if cap_def:
                derived |= cap_def.required_backends
        if frozenset(derived) != skill_info.backend_requirements:
            violations.append(
                f"{skill_info.name}: derived={sorted(derived)}, "
                f"actual={sorted(skill_info.backend_requirements)}"
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
        if cap.codex_status == "not-applicable":
            if cap.required_backends != frozenset({AGENT_BACKEND_CLAUDE_CODE}):
                violations.append(
                    f"{key}: codex_status='not-applicable' but "
                    f"required_backends={cap.required_backends!r} (expected {{claude-code}})"
                )
        else:
            if cap.required_backends != frozenset():
                violations.append(
                    f"{key}: codex_status={cap.codex_status!r} but "
                    f"required_backends={cap.required_backends!r} (expected empty)"
                )
    assert not violations, "\n".join(f"  {v}" for v in violations)


_NOT_APPLICABLE_MCP_CAPABILITIES = {"open_kitchen"}


def test_mcp_tools_require_claude_code():
    from autoskillit.core.types._type_constants_env import AGENT_BACKEND_CLAUDE_CODE

    violations = []
    for cap_name in _NOT_APPLICABLE_MCP_CAPABILITIES:
        cap = SKILL_CAPABILITY_REGISTRY.get(cap_name)
        if cap and cap.required_backends != frozenset({AGENT_BACKEND_CLAUDE_CODE}):
            violations.append(
                f"{cap_name}: expected required_backends=frozenset({{'claude-code'}}), "
                f"got {cap.required_backends!r}"
            )
    assert not violations, (
        "Not-applicable MCP capabilities must derive required_backends"
        " from codex_status='not-applicable':\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_required_backends_is_derived_property():
    from autoskillit.core.types._type_constants_registries import SkillCapabilityDef

    assert "required_backends" not in SkillCapabilityDef.__dataclass_fields__


def test_headless_tools_not_marked_not_applicable():
    from autoskillit.core.types._type_constants_registries import HEADLESS_TOOLS

    violations = []
    for tool_name in sorted(HEADLESS_TOOLS):
        cap = SKILL_CAPABILITY_REGISTRY.get(tool_name)
        if cap and cap.codex_status == "not-applicable":
            violations.append(f"{tool_name}: in HEADLESS_TOOLS but codex_status='not-applicable'")
    assert not violations, (
        "HEADLESS_TOOLS are accessible in Codex headless sessions — "
        "codex_status='not-applicable' is contradictory:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_test_check_codex_status_is_works_as_is():
    assert SKILL_CAPABILITY_REGISTRY["test_check"].codex_status == "works-as-is", (
        "test_check must be classified as works-as-is, not just != not-applicable"
    )


def test_run_skill_codex_status_is_works_as_is():
    assert SKILL_CAPABILITY_REGISTRY["run_skill"].codex_status == "works-as-is", (
        "run_skill must be classified as works-as-is — it is callable from "
        "Codex ORCHESTRATOR+HEADLESS sessions via kitchen-core tag visibility"
    )


_CODEX_SESSION_CONTEXTS: list[tuple[str, dict[str, str]]] = [
    (
        "skill+headless+autogate",
        {
            "AUTOSKILLIT_SESSION_TYPE": "skill",
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1",
        },
    ),
    (
        "orchestrator+headless",
        {
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
            "AUTOSKILLIT_HEADLESS": "1",
        },
    ),
    (
        "orchestrator+headless+tags",
        {
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "plan-review",
        },
    ),
    (
        "fleet",
        {
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
        },
    ),
]

_TESTED_SESSION_TYPES = {
    env_vars.get("AUTOSKILLIT_SESSION_TYPE", "skill") for _, env_vars in _CODEX_SESSION_CONTEXTS
}

_ALL_SESSION_TYPES = {st.value for st in SessionType}

_UNTESTED_SESSION_TYPES = _ALL_SESSION_TYPES - _TESTED_SESSION_TYPES


def test_all_session_types_covered_by_visibility_matrix():
    assert not _UNTESTED_SESSION_TYPES, (
        f"Session types not covered by _CODEX_SESSION_CONTEXTS: "
        f"{sorted(_UNTESTED_SESSION_TYPES)}. Add a context entry for each."
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ctx_label,env_vars",
    _CODEX_SESSION_CONTEXTS,
    ids=[c[0] for c in _CODEX_SESSION_CONTEXTS],
)
async def test_codex_status_vs_visibility_matrix(ctx_label, env_vars, monkeypatch):
    """No tag-gated tool visible in any Codex session context may be classified not-applicable.

    Scoped to GATED_TOOLS | HEADLESS_TOOLS — free-range tools (UNGATED_TOOLS like
    open_kitchen) are always visible regardless of session type, so their codex_status
    is not meaningfully testable via tag visibility alone.
    """
    from autoskillit.core.types._type_constants_registries import (
        GATED_TOOLS,
        HEADLESS_TOOLS,
    )
    from autoskillit.server import mcp
    from autoskillit.server._session_type import _apply_session_type_visibility

    for key, val in env_vars.items():
        monkeypatch.setenv(key, val)
    for key in (
        "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS",
        "AUTOSKILLIT_HEADLESS_AUTO_GATE",
        "AUTOSKILLIT_HEADLESS",
    ):
        if key not in env_vars:
            monkeypatch.delenv(key, raising=False)

    _apply_session_type_visibility()
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}

    tag_gated_caps = set(SKILL_CAPABILITY_REGISTRY) & (GATED_TOOLS | HEADLESS_TOOLS)

    violations = []
    for cap_name in sorted(tag_gated_caps):
        cap = SKILL_CAPABILITY_REGISTRY[cap_name]
        if cap_name in tool_names and cap.codex_status == "not-applicable":
            violations.append(
                f"{cap_name}: visible in {ctx_label} but codex_status='not-applicable'"
            )

    assert not violations, (
        f"Tag-gated tools visible in Codex {ctx_label} sessions must not be "
        f"classified as not-applicable:\n" + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.anyio
async def test_run_skill_visible_in_orchestrator_is_not_blocked(monkeypatch):
    """run_skill is visible in ORCHESTRATOR+HEADLESS — codex_status must not be not-applicable."""
    from autoskillit.server import mcp
    from autoskillit.server._session_type import _apply_session_type_visibility

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", raising=False)

    _apply_session_type_visibility()
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}

    assert "run_skill" in tool_names, "run_skill must be visible in ORCHESTRATOR+HEADLESS sessions"
    cap = SKILL_CAPABILITY_REGISTRY["run_skill"]
    assert cap.codex_status != "not-applicable", (
        "run_skill is visible in Codex ORCHESTRATOR+HEADLESS sessions — "
        "codex_status='not-applicable' is a misclassification"
    )


def test_reclassified_skills_have_empty_backend_requirements():
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    previously_blocked = [
        "dry-walkthrough",
        "plan-experiment",
        "planner-elaborate-assignments",
        "select-directions",
        "setup-project",
    ]
    violations = []
    for skill_name in previously_blocked:
        info = resolver.resolve(skill_name)
        assert info is not None, f"Skill {skill_name!r} not found by resolver"
        if info.backend_requirements != frozenset():
            violations.append(
                f"{skill_name}: backend_requirements={info.backend_requirements!r}, "
                f"expected frozenset()"
            )
    assert not violations, (
        "Previously blocked skills must have empty backend_requirements:\n"
        + "\n".join(f"  {v}" for v in violations)
    )

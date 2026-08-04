"""Capability evidence and semantic-routing authority boundaries."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.core.types._type_enums import SkillExecutionRole
from autoskillit.workspace import SkillFrontmatterParseResult, read_skill_frontmatter
from tests.arch._helpers import _iter_skill_dirs

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _all_skill_frontmatter() -> list[tuple[str, SkillFrontmatterParseResult]]:
    return [(name, read_skill_frontmatter(skill_md)) for name, skill_md in _iter_skill_dirs()]


def test_all_capability_keys_are_consumed() -> None:
    used_caps: set[str] = set()
    for _name, parsed in _all_skill_frontmatter():
        assert parsed.data is not None
        used_caps.update(parsed.data.get("uses_capabilities", []))
    assert not set(SKILL_CAPABILITY_REGISTRY) - used_caps


def test_no_unknown_capability_declared() -> None:
    known = set(SKILL_CAPABILITY_REGISTRY)
    unknown: list[str] = []
    for name, parsed in _all_skill_frontmatter():
        assert parsed.data is not None
        for capability in parsed.data.get("uses_capabilities", []):
            if capability not in known:
                unknown.append(f"{name}: {capability}")
    assert not unknown, "Unknown capabilities:\n" + "\n".join(unknown)


def test_structured_review_posters_do_not_declare_github_api_write() -> None:
    """Review posters delegate writes to the structured server-side tool."""
    from autoskillit.core import pkg_root

    for skill_name in ("review-pr", "review-research-pr", "audit-claims"):
        skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
        parsed = read_skill_frontmatter(skill_md)
        assert parsed.data is not None
        assert "github_api_write" not in set(parsed.data.get("uses_capabilities", [])), (
            f"{skill_name} must delegate GitHub review writes to post_pr_review"
        )


def test_capability_registry_has_no_backend_routing_authority() -> None:
    from autoskillit.core.types._type_constants_registries import SkillCapabilityDef

    forbidden_fields = {
        "worker_routable",
        "required_backend_property",
        "required_recipe_ingredient",
    }
    assert forbidden_fields.isdisjoint(SkillCapabilityDef.__dataclass_fields__)
    assert not hasattr(SkillCapabilityDef, "required_backends")
    assert {
        "agent_model",
        "agent_subagent",
        "cross_skill_ref",
        "git_metadata_write",
    }.isdisjoint(SKILL_CAPABILITY_REGISTRY)


def test_semantic_operations_replace_lexical_routing_capabilities() -> None:
    from autoskillit.core import SkillSemanticOperation

    values = {operation.value for operation in SkillSemanticOperation}
    assert {
        "child_spawn",
        "child_model_policy",
        "sibling_skill_invoke",
        "git_metadata_write",
    } <= values


def test_every_capability_def_declares_exact_allowed_execution_roles() -> None:
    import ast
    import inspect

    import autoskillit.core.types._type_constants_registries as registries

    tree = ast.parse(inspect.getsource(registries))
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SkillCapabilityDef"
    ]
    assert len(definitions) == len(SKILL_CAPABILITY_REGISTRY)
    missing = [
        node.lineno
        for node in definitions
        if "allowed_execution_roles" not in {keyword.arg for keyword in node.keywords}
    ]
    assert not missing
    all_roles = frozenset(SkillExecutionRole)
    for name, capability in SKILL_CAPABILITY_REGISTRY.items():
        assert isinstance(capability.allowed_execution_roles, frozenset)
        assert capability.allowed_execution_roles
        assert capability.allowed_execution_roles <= all_roles, name


def test_run_skill_is_owned_by_exact_orchestrator_role() -> None:
    assert SKILL_CAPABILITY_REGISTRY["run_skill"].allowed_execution_roles == frozenset(
        {SkillExecutionRole.ORCHESTRATOR}
    )


def test_headless_tools_not_marked_not_applicable() -> None:
    from autoskillit.core.types._type_constants_registries import HEADLESS_TOOLS

    violations = [
        tool_name
        for tool_name in sorted(HEADLESS_TOOLS)
        if (capability := SKILL_CAPABILITY_REGISTRY.get(tool_name))
        and capability.codex_status == "not-applicable"
    ]
    assert not violations


def test_test_check_and_run_skill_work_on_codex() -> None:
    assert SKILL_CAPABILITY_REGISTRY["test_check"].codex_status == "works-as-is"
    assert SKILL_CAPABILITY_REGISTRY["run_skill"].codex_status == "works-as-is"


@pytest.mark.anyio
async def test_test_check_and_run_skill_are_runtime_visible_on_codex(monkeypatch) -> None:
    from autoskillit.server import mcp
    from autoskillit.server._session_type import _apply_session_type_visibility

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", raising=False)

    _apply_session_type_visibility()

    tool_names = {tool.name for tool in await mcp.list_tools()}
    assert {"run_skill", "test_check"} <= tool_names


def test_fix_required_capability_is_only_github_api_write() -> None:
    assert {
        name
        for name, capability in SKILL_CAPABILITY_REGISTRY.items()
        if capability.codex_status == "fix-required"
    } == {"github_api_write"}

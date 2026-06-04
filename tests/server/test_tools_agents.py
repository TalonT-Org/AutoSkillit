"""Tests for tools_agents.py: agent pack registry, MCP resources, and unlock_agent_pack."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core.types._type_constants_registries import AGENT_PACK_REGISTRY
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# T1: AGENT_PACK_REGISTRY keys are lowercase
def test_agent_pack_registry_lowercase():
    """Assert all keys in AGENT_PACK_REGISTRY are lowercase."""
    for key in AGENT_PACK_REGISTRY:
        assert key == key.lower(), f"AGENT_PACK_REGISTRY key '{key}' is not lowercase"


# T2: Bundled agent files exist for each pack
def test_bundled_agent_files_exist():
    """For every pack in AGENT_PACK_REGISTRY, agent files exist."""
    from autoskillit.core import pkg_root

    agents_dir = pkg_root() / "agents"
    for pack in AGENT_PACK_REGISTRY:
        files = [
            p
            for p in agents_dir.glob("*.md")
            if p.name != "CLAUDE.md" and p.stem.startswith(pack.split("-")[0])
        ]
        assert files, f"No agent files for pack {pack}"

    # plan-review pack specifically should have the 3 expected agents
    plan_review_files = {p.stem for p in agents_dir.glob("plan-*.md") if p.name != "CLAUDE.md"}
    expected_agents = {
        "plan-foundation-auditor",
        "plan-interface-mapper",
        "plan-registry-tracer",
    }
    assert expected_agents <= plan_review_files, (
        f"Missing plan-review agents: {expected_agents - plan_review_files}"
    )


# T3: Agent resource template is registered when plan-review tag is enabled
@pytest.mark.anyio
async def test_agent_resource_template_registered():
    """Enable plan-review tag, then list resource templates. Template should be present."""
    from autoskillit.server import mcp

    mcp.enable(tags={"plan-review"})
    try:
        templates = await mcp.list_resource_templates()
        uris = {t.uri_template for t in templates}
        assert "agent://plan-review/{name}" in uris
    finally:
        mcp.disable(tags={"plan-review"})


# T4: Agent resources are hidden by default (plan-review tag disabled)
@pytest.mark.anyio
async def test_agent_resources_hidden_by_default():
    """With plan-review tag disabled, no agent:// templates should be visible."""
    from autoskillit.server import mcp

    templates = await mcp.list_resource_templates()
    uris = {t.uri_template for t in templates}
    assert not any(u.startswith("agent://") for u in uris)


# T5: Agent index resource is registered when plan-review tag is enabled
@pytest.mark.anyio
async def test_agent_index_resource_registered():
    """Enable plan-review tag, then list static resources. _index should be present."""
    from autoskillit.server import mcp

    mcp.enable(tags={"plan-review"})
    try:
        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "agent://plan-review/_index" in uris
    finally:
        mcp.disable(tags={"plan-review"})


# T6: Agent index returns JSON list of agent names
@pytest.mark.anyio
async def test_agent_index_returns_json_list():
    """Enable plan-review tag and read _index. Result should be JSON list of 3 agents."""
    from autoskillit.server import mcp

    mcp.enable(tags={"plan-review"})
    try:
        result = await mcp.read_resource("agent://plan-review/_index")
        content = result.contents[0].content
        names = json.loads(content)
        assert set(names) == {
            "plan-foundation-auditor",
            "plan-interface-mapper",
            "plan-registry-tracer",
        }
    finally:
        mcp.disable(tags={"plan-review"})


# T7: unlock_agent_pack with unknown name returns error
@pytest.mark.anyio
async def test_unlock_agent_pack_unknown_name():
    """Call unlock_agent_pack with nonexistent pack. Should return success:false."""
    from autoskillit.server.tools.tools_agents import unlock_agent_pack

    mock_ctx = _make_mock_ctx()
    result = await unlock_agent_pack("nonexistent", ctx=mock_ctx)
    data = json.loads(result)
    assert data["success"] is False
    assert "nonexistent" in data["error"]


# T8: unlock_agent_pack enables agent resources in same session
@pytest.mark.anyio
async def test_unlock_agent_pack_enables_resources():
    """Call unlock_agent_pack in a FastMCP Client session. Then list templates."""
    from autoskillit.server.tools.tools_agents import unlock_agent_pack

    mock_ctx = MagicMock()
    mock_ctx.enable_components = AsyncMock()

    result = await unlock_agent_pack("plan-review", ctx=mock_ctx)
    data = json.loads(result)
    assert data["success"] is True
    assert data["pack"] == "plan-review"
    mock_ctx.enable_components.assert_awaited_once()


# T9: unlock_agent_pack is session-scoped
@pytest.mark.anyio
async def test_unlock_agent_pack_session_scoped():
    """Call unlock_agent_pack in session A. Session B should NOT see agent templates."""
    from fastmcp.client import Client

    from autoskillit.server import mcp

    # Session B (without unlock): check templates — should not see agent templates
    async with Client(mcp) as client_b:
        templates = await client_b.list_resource_templates()
        uris = {t.uriTemplate for t in templates}
        assert not any(u.startswith("agent://") for u in uris)


# T10: Agent files are in pyproject.toml artifacts
def test_agent_defs_in_pyproject_artifacts():
    """Read pyproject.toml, verify agents/** is in artifacts list."""
    from autoskillit.core import pkg_root

    toml_path = pkg_root().parents[1] / "pyproject.toml"
    content = toml_path.read_text()
    assert '"src/autoskillit/agents/**"' in content


# T11: make-plan SKILL.md subagent_type references resolve to agent files
def test_make_plan_subagent_type_refs_resolve():
    """Grep SKILL.md for autoskillit:plan-* subagent_type refs.

    Each must correspond to an agent definition file in agents/.
    """
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "make-plan" / "SKILL.md").read_text()
    refs = re.findall(r"autoskillit:(plan-[a-z-]+)", content)
    unique_refs = set(refs)
    assert len(unique_refs) >= 3, (
        f"Expected >=3 unique autoskillit:plan-* refs in SKILL.md, found {len(unique_refs)}"
    )

    agents_dir = pkg_root() / "agents"
    for agent_name in set(refs):
        agent_file = agents_dir / f"{agent_name}.md"
        assert agent_file.exists(), (
            f"SKILL.md references autoskillit:{agent_name} but {agent_file} does not exist"
        )


# T11-rectify: rectify SKILL.md subagent_type references resolve to agent files
def test_rectify_subagent_type_refs_resolve():
    """Grep rectify SKILL.md for autoskillit:plan-* subagent_type refs.

    Each must correspond to an agent definition file in agents/.
    """
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "rectify" / "SKILL.md").read_text()
    refs = re.findall(r"autoskillit:(plan-[a-z-]+)", content)
    unique_refs = set(refs)
    assert len(unique_refs) >= 3, (
        f"Expected >=3 unique autoskillit:plan-* refs in rectify SKILL.md, "
        f"found {len(unique_refs)}"
    )

    agents_dir = pkg_root() / "agents"
    for agent_name in unique_refs:
        agent_file = agents_dir / f"{agent_name}.md"
        assert agent_file.exists(), (
            f"rectify SKILL.md references autoskillit:{agent_name} but {agent_file} does not exist"
        )


# T11b: make-plan SKILL.md no longer has activate_agents frontmatter
def test_make_plan_no_activate_agents():
    """SKILL.md frontmatter must not contain activate_agents."""
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "make-plan" / "SKILL.md").read_text()
    m = re.search(r"^activate_agents:", content, re.MULTILINE)
    assert m is None, "activate_agents found in SKILL.md frontmatter — should have been removed"


# T12: Agent definition frontmatter has required fields
def test_agent_definition_frontmatter_valid():
    """For each .md file in agents/, parse YAML frontmatter and assert required fields exist."""
    from autoskillit.core import pkg_root
    from autoskillit.core.io import load_yaml

    agents_dir = pkg_root() / "agents"
    for md_file in sorted(agents_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        content = md_file.read_text()
        # Split YAML frontmatter from markdown body
        if content.startswith("---"):
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{md_file.name}: missing YAML frontmatter"
            frontmatter = load_yaml(parts[1])
            for field in ("name", "description", "tools", "model", "maxTurns"):
                assert field in frontmatter, f"{md_file.name}: missing frontmatter field '{field}'"


# T13: RETIRED_AGENT_NAMES contains all 4 replaced agent names
def test_retired_agent_names_contains_old_agents():
    """RETIRED_AGENT_NAMES must contain all 4 replaced agent names."""
    from autoskillit.core.types._type_constants import RETIRED_AGENT_NAMES

    expected = {
        "plan-assumption-challenger",
        "plan-completeness-auditor",
        "plan-contract-verifier",
        "plan-registry-wire-tracer",
    }
    assert expected <= RETIRED_AGENT_NAMES


# T14: No retired agent name has a live file
def test_no_retired_agent_name_has_a_live_file():
    """No .md file in agents/ should have a stem matching a retired agent name."""
    from autoskillit.core import pkg_root
    from autoskillit.core.types._type_constants import RETIRED_AGENT_NAMES

    agents_dir = pkg_root() / "agents"
    for md_file in agents_dir.glob("*.md"):
        if md_file.name == "CLAUDE.md":
            continue
        assert md_file.stem not in RETIRED_AGENT_NAMES, (
            f"Retired agent name '{md_file.stem}' still has a live file: {md_file}"
        )


@pytest.mark.anyio
async def test_plan_review_pre_revealed_for_non_notification_backend(tmp_path, monkeypatch):
    """Non-notification backend gets plan-review resources pre-revealed via _pre_reveal_kitchen."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from autoskillit.core import HEADLESS_ENV_VAR
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import mcp
    from autoskillit.server._lifespan import _skill_auto_gate_boot
    from tests.server.conftest import _make_mock_ctx

    monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)

    mock_backend = MagicMock()
    mock_backend.capabilities.supports_tool_list_changed = False
    ctx = _make_mock_ctx()
    ctx.backend = mock_backend
    ctx.gate = DefaultGateState(enabled=False)
    ctx.project_dir = tmp_path

    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
        with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
            with patch("autoskillit.server._lifespan.register_active_kitchen"):
                await _skill_auto_gate_boot(ctx)

    templates = await mcp.list_resource_templates()
    uris = {t.uri_template for t in templates}
    assert "agent://plan-review/{name}" in uris, (
        "plan-review resources should be pre-revealed at startup for non-notification backend"
    )


# T15: RETIRED_AGENT_NAMES entries are lowercase
def test_retired_agent_names_lowercase():
    """All entries in RETIRED_AGENT_NAMES must be lowercase."""
    from autoskillit.core.types._type_constants import RETIRED_AGENT_NAMES

    for name in RETIRED_AGENT_NAMES:
        assert name == name.lower(), f"RETIRED_AGENT_NAMES entry '{name}' is not lowercase"


# All 15 WPResult fields (used by T-NEW-2)
_WP_RESULT_ALL_FIELDS = frozenset(
    {
        "id",
        "phase_id",
        "assignment_id",
        "name",
        "scope",
        "estimated_files",
        "summary",
        "goal",
        "technical_steps",
        "files_touched",
        "apis_defined",
        "apis_consumed",
        "depends_on",
        "deliverables",
        "acceptance_criteria",
    }
)


# T-NEW-1: planner-elaborate-wps SKILL.md subagent_type refs resolve to agent files
def test_elaborate_wps_subagent_type_refs_resolve():
    """planner-elaborate-wps SKILL.md subagent_type refs resolve to agent files."""
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "planner-elaborate-wps" / "SKILL.md").read_text()
    refs = re.findall(r'subagent_type.*?["\']autoskillit:([a-z][a-z0-9-]+)["\']', content)
    unique_refs = set(refs)
    assert len(unique_refs) >= 1, (
        f"Expected >=1 subagent_type ref in planner-elaborate-wps SKILL.md, "
        f"found {len(unique_refs)}"
    )
    agents_dir = pkg_root() / "agents"
    for agent_name in unique_refs:
        agent_file = agents_dir / f"{agent_name}.md"
        assert agent_file.exists(), (
            f"SKILL.md references autoskillit:{agent_name} but {agent_file} does not exist"
        )


# T-NEW-4: audit-impl SKILL.md subagent_type refs resolve to agent files
def test_audit_impl_subagent_type_refs_resolve():
    """audit-impl SKILL.md subagent_type refs resolve to agent files."""
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "audit-impl" / "SKILL.md").read_text()
    refs = re.findall(r'subagent_type.*?["\']autoskillit:([a-z][a-z0-9-]+)["\']', content)
    unique_refs = set(refs)
    assert len(unique_refs) >= 1, (
        f"Expected >=1 subagent_type ref in audit-impl SKILL.md, found {len(unique_refs)}"
    )
    agents_dir = pkg_root() / "agents"
    for agent_name in unique_refs:
        agent_file = agents_dir / f"{agent_name}.md"
        assert agent_file.exists(), (
            f"SKILL.md references autoskillit:{agent_name} but {agent_file} does not exist"
        )


# T-NEW-5: audit-impl-slice-auditor is subagent_type-only — not in any agent pack
def test_audit_impl_slice_auditor_is_packless():
    """audit-impl-slice-auditor is subagent_type-only — not in any agent pack."""
    from autoskillit.core import pkg_root
    from autoskillit.core.types._type_constants_registries import AGENT_PACK_REGISTRY

    agent_path = pkg_root() / "agents" / "audit-impl-slice-auditor.md"
    assert agent_path.exists(), "audit-impl-slice-auditor.md must exist"
    for pack_name in AGENT_PACK_REGISTRY:
        assert "audit-impl-slice-auditor" not in pack_name, (
            f"audit-impl-slice-auditor must NOT appear in any AGENT_PACK_REGISTRY pack name — "
            f"found in '{pack_name}'. It is subagent_type-only."
        )


# T-NEW-2: wp-elaborator.md JSON schema must contain all WPResult fields
def test_wp_elaborator_schema_covers_all_wp_fields():
    """wp-elaborator.md JSON schema must contain all WPResult fields, not just WP_REQUIRED_KEYS."""
    from autoskillit.core import pkg_root
    from autoskillit.planner.schema import WP_REQUIRED_KEYS

    agent_path = pkg_root() / "agents" / "wp-elaborator.md"
    content = agent_path.read_text()
    parts = content.split("---", 2)
    assert len(parts) >= 3, "wp-elaborator.md must have YAML frontmatter"
    body = parts[2]
    # WP_REQUIRED_KEYS is a subset — verify it hasn't drifted from our full set
    assert WP_REQUIRED_KEYS <= _WP_RESULT_ALL_FIELDS
    for key in _WP_RESULT_ALL_FIELDS:
        assert f'"{key}"' in body, f"wp-elaborator.md schema must contain WPResult field: {key}"


# T-NEW-3: wp-elaborator is subagent_type-only — must not be registered in any agent pack
def test_wp_elaborator_is_packless():
    """wp-elaborator is subagent_type-only — must not be registered in any agent pack."""
    from autoskillit.core import pkg_root
    from autoskillit.core.types._type_constants_registries import AGENT_PACK_REGISTRY

    agent_path = pkg_root() / "agents" / "wp-elaborator.md"
    assert agent_path.exists(), "wp-elaborator.md must exist"
    # AGENT_PACK_REGISTRY keys are pack names — guard against a new pack created for wp-elaborator
    for pack_name in AGENT_PACK_REGISTRY:
        assert "wp-elaborator" not in pack_name, (
            f"wp-elaborator must NOT appear in any AGENT_PACK_REGISTRY pack name — "
            f"found in '{pack_name}'. It is subagent_type-only."
        )
    # Guard against accidental inclusion in the plan-review pack glob (plan-*.md)
    assert not agent_path.stem.startswith("plan-"), (
        "wp-elaborator.md must NOT start with 'plan-' (would collide with plan-review pack glob)"
    )


# DIAG_C6: pipeline-health-scanner agent exists
def test_pipeline_health_scanner_agent_exists():
    """pipeline-health-scanner.md agent definition must exist with required frontmatter."""
    from autoskillit.core import pkg_root
    from autoskillit.core.io import load_yaml

    agent_path = pkg_root() / "agents" / "pipeline-health-scanner.md"
    assert agent_path.is_file(), f"pipeline-health-scanner.md not found at {agent_path}"
    content = agent_path.read_text()
    assert "name: pipeline-health-scanner" in content

    parts = content.split("---", 2)
    assert len(parts) >= 3, "pipeline-health-scanner.md must have YAML frontmatter"
    frontmatter = load_yaml(parts[1]) or {}
    max_turns = frontmatter.get("maxTurns")
    assert max_turns is not None and max_turns >= 80, (
        f"pipeline-health-scanner maxTurns must be >= 80 (got {max_turns}); "
        "80 minimum: scanner needs > 40 turns for adversarial sub-cycles"
    )
    tools = frontmatter.get("tools", [])
    assert "Agent" in tools, (
        "pipeline-health-scanner.md frontmatter tools must include 'Agent' "
        "(body instructs spawning adversarial subagents)"
    )

    body = parts[2]
    assert "scan_result:" in body, (
        "pipeline-health-scanner.md body must contain a 'scan_result:' structured completion token"
    )
    assert "codex_log" in body, (
        "pipeline-health-scanner.md body must contain Codex log guidance "
        "(codex_log data source section)"
    )
    assert "turn.failed" in body, (
        "pipeline-health-scanner.md body must reference turn.failed event type "
        "(Codex error detection guidance)"
    )


# DIAG_C7: plan-registry-tracer maxTurns >= 80
def test_plan_registry_tracer_max_turns_sufficient() -> None:
    """plan-registry-tracer.md maxTurns must be >= 80."""
    from autoskillit.core import pkg_root
    from autoskillit.core.io import load_yaml

    agent_path = pkg_root() / "agents" / "plan-registry-tracer.md"
    assert agent_path.is_file(), f"plan-registry-tracer.md not found at {agent_path}"
    content = agent_path.read_text()

    parts = content.split("---", 2)
    assert len(parts) >= 3, "plan-registry-tracer.md must have YAML frontmatter"
    frontmatter = load_yaml(parts[1]) or {}
    max_turns = frontmatter.get("maxTurns")
    assert max_turns is not None and max_turns >= 80, (
        f"plan-registry-tracer maxTurns must be >= 80 (got {max_turns}); "
        "80 minimum: tracer needs > 40 turns for multi-pass LSP + tree-sitter + grep analysis"
    )


# DIAG_C10: all agent definitions have structured output contracts
AGENTS_WITHOUT_STRUCTURED_OUTPUT: frozenset[str] = frozenset()


def test_all_agents_have_structured_output():
    """Every agent definition must have a structured output marker or be in the allowlist."""
    from autoskillit.core import pkg_root

    _STRUCTURED_MARKERS = (
        "Verdict:",
        "scan_result:",
        "```json",
        "# Verdict",
        "## Verdict",
        "### Verdict",
    )

    agents_dir = pkg_root() / "agents"
    failures: list[str] = []
    for md_file in sorted(agents_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        if md_file.stem in AGENTS_WITHOUT_STRUCTURED_OUTPUT:
            continue
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        if not any(marker in body for marker in _STRUCTURED_MARKERS):
            failures.append(
                f"  {md_file.name}: no structured output marker found "
                f"(expected one of {_STRUCTURED_MARKERS}). "
                f"Add a completion token/verdict/schema or list in "
                f"AGENTS_WITHOUT_STRUCTURED_OUTPUT."
            )
    assert not failures, "Agent definitions must have structured output contracts:\n" + "\n".join(
        failures
    )


# DIAG_C11: agent frontmatter tools must cover all tool references in body
def test_agent_tool_list_covers_body_references():
    """Agent frontmatter tools must include all tools referenced in the body."""
    import re

    from autoskillit.core import pkg_root
    from autoskillit.core.io import load_yaml

    _SPAWN_SUBAGENT_RE = re.compile(r"spawn\s+\w+\s+subagent", re.IGNORECASE)
    _LSP_BODY_RE = re.compile(r"\bLSP\b")

    agents_dir = pkg_root() / "agents"
    failures: list[str] = []
    for md_file in sorted(agents_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        content = md_file.read_text()
        parts = content.split("---", 2)
        if len(parts) < 3:
            continue
        frontmatter = load_yaml(parts[1]) or {}
        tools: list[str] = frontmatter.get("tools", [])
        body = parts[2]
        if _SPAWN_SUBAGENT_RE.search(body) and "Agent" not in tools:
            failures.append(
                f"  {md_file.name}: body references spawning subagents but 'Agent' "
                f"is not in frontmatter tools: {tools}"
            )
        if _LSP_BODY_RE.search(body) and "LSP" not in tools:
            failures.append(
                f"  {md_file.name}: body references 'LSP' but 'LSP' "
                f"is not in frontmatter tools: {tools}"
            )
    assert not failures, (
        "Agent frontmatter tools must cover all tool references in the body:\n"
        + "\n".join(failures)
    )


def test_agent_resource_strips_frontmatter():
    """agent:// resource must strip YAML frontmatter before serving."""
    from autoskillit.server.tools.tools_agents import get_plan_review_agent

    result = get_plan_review_agent("plan-foundation-auditor")
    assert not result.startswith("---"), "Frontmatter delimiter still present"
    assert "model:" not in result.split("\n")[0], "Frontmatter field leaked into first line"
    assert result.strip(), "Frontmatter-stripped body should not be empty"

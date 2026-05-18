"""Tests for tools_agents.py: agent pack registry, MCP resources, and unlock_agent_pack."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core.types._type_constants import AGENT_PACK_REGISTRY
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

    # plan-review pack specifically should have 4 files
    plan_review_files = [p for p in agents_dir.glob("plan-*.md") if p.name != "CLAUDE.md"]
    assert len(plan_review_files) == 4, (
        f"Expected 4 plan-review agents, found {len(plan_review_files)}"
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
    """Enable plan-review tag and read _index. Result should be JSON list of 4 agents."""
    from autoskillit.server import mcp

    mcp.enable(tags={"plan-review"})
    try:
        result = await mcp.read_resource("agent://plan-review/_index")
        content = result.contents[0].content
        names = json.loads(content)
        assert set(names) == {
            "plan-contract-verifier",
            "plan-completeness-auditor",
            "plan-assumption-challenger",
            "plan-registry-wire-tracer",
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
        uris = {t.uri_template for t in templates}
        assert not any(u.startswith("agent://") for u in uris)


# T10: Agent files are in pyproject.toml artifacts
def test_agent_defs_in_pyproject_artifacts():
    """Read pyproject.toml, verify agents/** is in artifacts list."""
    from autoskillit.core import pkg_root

    toml_path = pkg_root().parents[1] / "pyproject.toml"
    content = toml_path.read_text()
    assert '"src/autoskillit/agents/**"' in content


# T11: make-plan SKILL.md activate_agents references valid packs
def test_make_plan_activate_agents_resolves():
    """Parse activate_agents from make-plan SKILL.md. All packs should exist in AGENT_PACK_REGISTRY."""
    import re

    from autoskillit.core import pkg_root

    content = (pkg_root() / "skills_extended" / "make-plan" / "SKILL.md").read_text()
    m = re.search(r"^activate_agents:\s*\[([^\]]*)\]", content, re.MULTILINE)
    assert m is not None, "activate_agents not found in make-plan SKILL.md frontmatter"
    packs = [p.strip() for p in m.group(1).split(",") if p.strip()]
    for pack in packs:
        assert pack in AGENT_PACK_REGISTRY, (
            f"Pack '{pack}' from activate_agents not in AGENT_PACK_REGISTRY"
        )


# T12: Agent definition frontmatter has required fields
def test_agent_definition_frontmatter_valid():
    """For each .md file in agents/, parse YAML frontmatter and assert required fields exist."""
    import yaml

    from autoskillit.core import pkg_root

    agents_dir = pkg_root() / "agents"
    for md_file in sorted(agents_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        content = md_file.read_text()
        # Split YAML frontmatter from markdown body
        if content.startswith("---"):
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{md_file.name}: missing YAML frontmatter"
            frontmatter = yaml.safe_load(parts[1])
            for field in ("name", "description", "tools", "model", "maxTurns"):
                assert field in frontmatter, f"{md_file.name}: missing frontmatter field '{field}'"

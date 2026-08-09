"""Agent tool-name surface alignment: authored, projected, and registered tools must agree."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    DIRECT_PREFIX,
    EXPLORATION_TOOLS,
    MARKETPLACE_PREFIX,
    SkillExecutionRole,
    SkillSource,
    load_agent_definitions,
    load_bundled_agent_definitions,
    pkg_root,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_authored_agent_mcp_tools_derive_from_core_authority() -> None:
    """Every MCP tool in a bundled agent definition equals DIRECT_PREFIX + short_name."""
    definitions = load_bundled_agent_definitions()
    for definition in definitions:
        for tool in definition.tools:
            if not tool.startswith("mcp__"):
                continue
            assert tool.startswith(DIRECT_PREFIX), (
                f"Agent {definition.name!r} tool {tool!r} does not use "
                f"the canonical direct prefix {DIRECT_PREFIX!r}"
            )
            short = tool[len(DIRECT_PREFIX) :]
            assert short in EXPLORATION_TOOLS, (
                f"Agent {definition.name!r} tool short name {short!r} "
                f"is not a registered exploration tool"
            )


def test_marketplace_artifact_agent_tools_carry_marketplace_prefix(tmp_path: Path) -> None:
    """The marketplace-published agent definitions carry MARKETPLACE_PREFIX."""
    from autoskillit.workspace import (
        SkillProjectionContext,
        materialize_sanitized_plugin_root,
    )
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_root = pkg_root()
    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    destination = tmp_path / "plugins" / "autoskillit"
    destination.parent.mkdir(parents=True)
    materialize_sanitized_plugin_root(
        source_root,
        destination,
        catalog,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
        mcp_tool_prefix=MARKETPLACE_PREFIX,
    )

    projected_agents_dir = destination / "agents"
    assert projected_agents_dir.is_dir()
    projected_defs = load_agent_definitions(projected_agents_dir)
    assert projected_defs, "Expected at least one agent definition in the projected artifact"

    for definition in projected_defs:
        for tool in definition.tools:
            if not tool.startswith("mcp__"):
                continue
            assert tool.startswith(MARKETPLACE_PREFIX), (
                f"Marketplace agent {definition.name!r} tool {tool!r} does not use "
                f"the marketplace prefix {MARKETPLACE_PREFIX!r}"
            )
            short = tool[len(MARKETPLACE_PREFIX) :]
            assert short in EXPLORATION_TOOLS, (
                f"Marketplace agent {definition.name!r} tool short name {short!r} "
                f"is not a registered exploration tool"
            )

"""T13: explorer role bodies must open with a mandatory conformance preamble."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    EXPLORATION_TOOLS,
    find_qualified_autoskillit_tool_names,
    load_agent_definition,
    load_bundled_agent_definitions,
    pkg_root,
    validate_agent_tool_canonical,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("role", sorted(BUNDLED_EXPLORER_ROLES))
def test_explorer_role_body_opens_with_conformance_preamble(role: str) -> None:
    """Each explorer role must open with a tool-surface self-check block."""
    definition = load_agent_definition(pkg_root() / "agents" / f"{role}.md")
    body = definition.body

    assert "Tool-surface conformance" in body, (
        f"Explorer role {role!r} body must contain a conformance preamble section"
    )
    assert "CONTRACT VIOLATION" in body, (
        f"Explorer role {role!r} preamble must contain the structured violation report"
    )
    assert "mandatory first action" in body.lower(), (
        f"Explorer role {role!r} preamble must indicate it is the mandatory first action"
    )

    for tool_short_name in sorted(EXPLORATION_TOOLS):
        assert tool_short_name in body, (
            f"Explorer role {role!r} preamble must reference tool {tool_short_name!r} "
            f"derived from frontmatter"
        )


_MCP_TOOL_AGENTS = sorted(
    definition.name
    for definition in load_bundled_agent_definitions()
    if any(tool.startswith("mcp__") for tool in definition.tools)
)


@pytest.mark.parametrize("agent", _MCP_TOOL_AGENTS)
def test_agent_bodies_name_frontmatter_mcp_tools_by_short_name(agent: str) -> None:
    """Bodies name frontmatter MCP tools by short name, never a corridor-specific prefix."""
    definition = load_agent_definition(pkg_root() / "agents" / f"{agent}.md")

    for tool in definition.tools:
        if not tool.startswith("mcp__"):
            continue
        short = validate_agent_tool_canonical(tool)
        assert f"`{short}`" in definition.body, (
            f"Agent {agent!r} body must reference frontmatter tool {tool!r} by its "
            f"short name `{short}`"
        )
    qualified = find_qualified_autoskillit_tool_names(definition.body)
    assert not qualified, (
        f"Agent {agent!r} body names qualified AutoSkillit tools {qualified!r}; the "
        "qualified name differs per corridor"
    )

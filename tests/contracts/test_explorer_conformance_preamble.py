"""T13: explorer role bodies must open with a mandatory conformance preamble."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    EXPLORATION_TOOLS,
    load_agent_definition,
    pkg_root,
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


@pytest.mark.parametrize("role", sorted(BUNDLED_EXPLORER_ROLES))
def test_conformance_preamble_derives_tools_from_frontmatter(role: str) -> None:
    """The conformance block references the same tools as the frontmatter."""
    definition = load_agent_definition(pkg_root() / "agents" / f"{role}.md")

    for tool in definition.tools:
        if not tool.startswith("mcp__"):
            continue
        assert tool in definition.body, (
            f"Explorer role {role!r} preamble must reference frontmatter tool {tool!r} "
            f"(no independent literals)"
        )

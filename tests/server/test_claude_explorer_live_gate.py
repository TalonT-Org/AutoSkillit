"""T14: Claude live conformance gate for session-scoped explorer authority.

Env-gated: runs only when AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE=1.
"""

from __future__ import annotations

import os

import pytest

from autoskillit.core import BUNDLED_EXPLORER_ROLES, EXPLORATION_TOOLS

_LIVE_ENV = "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE"
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1",
    reason=f"Claude explorer live gate requires {_LIVE_ENV}=1",
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@_skip_unless_live_gate
@pytest.mark.smoke
def test_explorer_tool_surface_exact() -> None:
    """The effective tool surface must be exactly the three broker tools."""
    from autoskillit.core import load_bundled_agent_definitions

    for definition in load_bundled_agent_definitions():
        if definition.name not in BUNDLED_EXPLORER_ROLES:
            continue
        tool_short_names = frozenset(
            tool.split("__")[-1] for tool in definition.tools if tool.startswith("mcp__")
        )
        assert tool_short_names == EXPLORATION_TOOLS, (
            f"role {definition.name} must declare exactly the three broker tools, "
            f"got {tool_short_names}"
        )

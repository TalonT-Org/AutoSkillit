"""Fleet session-type visibility tests.

Asserts fleet + FLEET_DISPATCH tools are visible for fleet sessions and hidden
for orchestrator / skill / food-truck / cook sessions. Co-locates the
"fleet-hides" regression guards because they require the
`@pytest.mark.feature("fleet")` marker that the conftest fixture does not gate.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.layer("server"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


@pytest.mark.anyio
async def test_fleet_dispatch_mode_enables_fleet_dispatch_tools(monkeypatch):
    """fleet + FLEET_MODE=dispatch reveals fleet tools + fleet-dispatch tools."""
    from autoskillit.core import (
        FLEET_DISPATCH_MODE,
        FLEET_DISPATCH_TOOLS,
        FLEET_MODE_ENV_VAR,
        FLEET_TOOLS,
        FREE_RANGE_TOOLS,
    )
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    monkeypatch.setenv(FLEET_MODE_ENV_VAR, FLEET_DISPATCH_MODE)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    visible = {t.name for t in tools}

    assert FLEET_TOOLS, "FLEET_TOOLS must be non-empty for this assertion to be meaningful"
    assert FLEET_DISPATCH_TOOLS, (
        "FLEET_DISPATCH_TOOLS must be non-empty for this assertion to be meaningful"
    )
    assert FREE_RANGE_TOOLS, (
        "FREE_RANGE_TOOLS must be non-empty for this assertion to be meaningful"
    )
    expected = FLEET_TOOLS | FLEET_DISPATCH_TOOLS | FREE_RANGE_TOOLS
    assert visible == expected


@pytest.mark.parametrize("mode_value", ["campaign", None])
@pytest.mark.anyio
async def test_fleet_campaign_mode_hides_fleet_dispatch_tools(monkeypatch, mode_value):
    """fleet + FLEET_MODE=campaign (or absent) hides fleet-dispatch tools."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    if mode_value is not None:
        monkeypatch.setenv(FLEET_MODE_ENV_VAR, mode_value)
    else:
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    visible = {t.name for t in tools}
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"fleet-dispatch tools unexpectedly visible with FLEET_MODE={mode_value!r}"
    )


@pytest.mark.anyio
async def test_fleet_dispatch_constant_matches_tagged_tools(monkeypatch):
    """FLEET_DISPATCH_TOOLS constant must exactly match tools tagged fleet-dispatch."""
    from autoskillit.core import FLEET_DISPATCH_MODE, FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    monkeypatch.setenv(FLEET_MODE_ENV_VAR, FLEET_DISPATCH_MODE)
    _apply_session_type_visibility()

    all_tools = {t.name: t for t in await mcp.list_tools()}
    tagged = {name for name, t in all_tools.items() if "fleet-dispatch" in t.tags}
    assert tagged == FLEET_DISPATCH_TOOLS, (
        f"FLEET_DISPATCH_TOOLS constant out of sync. "
        f"Extra in constant: {FLEET_DISPATCH_TOOLS - tagged}. "
        f"Extra on server: {tagged - FLEET_DISPATCH_TOOLS}."
    )


@pytest.mark.anyio
async def test_fleet_enables_fleet_tag(monkeypatch):
    from autoskillit.core import (
        EVIDENCE_READER_TOOLS,
        FLEET_TOOLS,
        GATED_TOOLS,
        HEADLESS_TOOLS,
    )
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    _apply_session_type_visibility()

    tool_names = {t.name for t in await mcp.list_tools()}

    # Positive: fleet-tagged tools are visible
    for name in FLEET_TOOLS:
        assert name in tool_names, f"{name} should be visible for fleet session"
    # Negative: non-fleet kitchen/headless tools remain hidden
    for name in GATED_TOOLS - FLEET_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for fleet session"
    for name in HEADLESS_TOOLS:
        assert name not in tool_names, f"{name} should be hidden for fleet session"
    assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)


@pytest.mark.anyio
async def test_fleet_tools_do_not_carry_kitchen_tag(monkeypatch):
    """Fleet-tagged tools must NOT carry the kitchen tag (tag partition)."""
    from autoskillit.core import FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    _apply_session_type_visibility()

    all_tools = {t.name: t for t in await mcp.list_tools()}
    for name in FLEET_TOOLS:
        tool = all_tools.get(name)
        assert tool is not None, f"{name} not registered"
        assert "kitchen" not in tool.tags, f"{name} must not carry kitchen tag"
        assert "fleet" in tool.tags, f"{name} must have fleet tag"
        assert "autoskillit" in tool.tags, f"{name} must retain autoskillit tag"


@pytest.mark.anyio
async def test_fleet_tools_constant_matches_tagged_tools(monkeypatch):
    """FLEET_TOOLS constant matches exactly the tools with fleet tag."""
    from autoskillit.core import FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    _apply_session_type_visibility()

    all_tools = {t.name: t for t in await mcp.list_tools()}
    tagged = {name for name, t in all_tools.items() if "fleet" in t.tags}
    assert tagged == FLEET_TOOLS, (
        f"FLEET_TOOLS constant out of sync. "
        f"Extra in constant: {FLEET_TOOLS - tagged}. "
        f"Extra on server: {tagged - FLEET_TOOLS}."
    )


# ---------------------------------------------------------------------------
# Regression guards — fleet tools must NOT be visible for non-fleet session types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrator_headless_hides_fleet_tools(monkeypatch):
    """Regression guard: fleet tools must NOT be visible in orchestrator+headless sessions."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    visible = {t.name for t in await mcp.list_tools()}
    assert visible.isdisjoint(FLEET_TOOLS), (
        f"Fleet tools visible in orchestrator+headless: {visible & FLEET_TOOLS}"
    )
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"Fleet-dispatch visible in orchestrator+headless: {visible & FLEET_DISPATCH_TOOLS}"
    )


@pytest.mark.anyio
async def test_orchestrator_interactive_hides_fleet_tools(monkeypatch):
    """Regression guard: fleet tools must NOT be visible in orchestrator+interactive
    sessions"""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    visible = {t.name for t in await mcp.list_tools()}
    assert visible.isdisjoint(FLEET_TOOLS), (
        f"Fleet tools visible in orchestrator+interactive: {visible & FLEET_TOOLS}"
    )
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"Fleet-dispatch visible in orchestrator+interactive: {visible & FLEET_DISPATCH_TOOLS}"
    )


@pytest.mark.anyio
async def test_skill_headless_hides_fleet_tools(monkeypatch):
    """Regression guard: fleet tools must NOT be visible in skill+headless sessions."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    visible = {t.name for t in await mcp.list_tools()}
    assert visible.isdisjoint(FLEET_TOOLS), (
        f"Fleet tools visible in skill+headless: {visible & FLEET_TOOLS}"
    )
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"Fleet-dispatch tools visible in skill+headless: {visible & FLEET_DISPATCH_TOOLS}"
    )


@pytest.mark.anyio
async def test_skill_interactive_hides_fleet_tools(monkeypatch):
    """Regression guard: fleet tools must NOT be visible in skill+interactive sessions."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    visible = {t.name for t in await mcp.list_tools()}
    assert visible.isdisjoint(FLEET_TOOLS), (
        f"Fleet tools visible in skill+interactive: {visible & FLEET_TOOLS}"
    )
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"Fleet-dispatch tools visible in skill+interactive: {visible & FLEET_DISPATCH_TOOLS}"
    )


@pytest.mark.anyio
async def test_no_session_type_hides_fleet_tools(monkeypatch):
    """Regression guard: fleet tools must NOT be visible when no session type is set."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
    _apply_session_type_visibility()

    visible = {t.name for t in await mcp.list_tools()}
    assert visible.isdisjoint(FLEET_TOOLS), (
        f"Fleet tools visible with no session type: {visible & FLEET_TOOLS}"
    )
    assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
        f"Fleet-dispatch tools visible with no session type: {visible & FLEET_DISPATCH_TOOLS}"
    )

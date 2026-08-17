"""Tests for the `_apply_session_type_visibility` session-type dispatch
contract — that it is the sole calling convention (no `feature_gates` parameter)
and that it activates fleet tag visibility for FLEET sessions.

The class-level `_reset_mcp_visibility` autouse fixture from the original
file is dropped here: the directory `tests/server/conftest.py`'s
`_reset_mcp_tags` autouse fixture already truncates `mcp._transforms` and
disables every gated tag (functionally identical to the dropped class-level
fixture).
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.layer("server"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


@pytest.mark.anyio
async def test_fleet_tools_visible_when_feature_enabled(monkeypatch):
    """SESSION_TYPE=fleet → fleet tools visible (session-type dispatch only)."""
    from autoskillit.core import FLEET_TOOLS
    from autoskillit.server import mcp
    from autoskillit.server._session_type import _apply_session_type_visibility

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    for name in FLEET_TOOLS:
        assert name in tool_names, f"{name} should be visible for fleet session (phase-1 reveal)"


def test_apply_session_type_visibility_sole_calling_convention():
    """No feature_gates parameter exists — session-type dispatch only."""
    import inspect

    from autoskillit.server._session_type import _apply_session_type_visibility

    sig = inspect.signature(_apply_session_type_visibility)
    assert "feature_gates" not in sig.parameters


@pytest.mark.anyio
async def test_session_type_fleet_enables_fleet_tags(monkeypatch):
    """FLEET session activates fleet tool visibility (no feature gate needed)."""
    from autoskillit.core import FLEET_TOOLS
    from autoskillit.server import mcp
    from autoskillit.server._session_type import _apply_session_type_visibility

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    _apply_session_type_visibility()

    tools = list(await mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert FLEET_TOOLS
    for tool in FLEET_TOOLS:
        assert tool in tool_names

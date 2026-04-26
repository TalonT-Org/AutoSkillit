"""Shared fixtures for tests/server/."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    """Reset module-level _ctx in server._state after each test.

    Tests that call _initialize() directly set _state._ctx to a mock without
    cleanup. Subsequent tests in the same xdist worker then find a stale mock
    _ctx, causing _apply_triage_gate to await a regular MagicMock and fail.

    monkeypatch records the current value before yield and restores it after,
    giving each test a clean slate regardless of what _initialize() sets.
    """
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", _state._ctx)


@pytest.fixture(autouse=True)
def _reset_mcp_tags():
    """Reset MCP tag visibility to default (kitchen disabled) before each test.

    The mcp singleton is process-global. Each mcp.enable()/disable() call appends
    a Visibility transform to an internal list — the list never shrinks. Over a
    full test suite (11k+ tests), thousands of accumulated transforms can cause
    version-dependent ordering issues in FastMCP's "last match wins" evaluation.

    Fix: truncate the transforms list back to a single entry matching the
    server/__init__.py import-time state: ``mcp.disable(tags={"kitchen"})``.
    """
    from autoskillit.server import mcp

    mcp._transforms.clear()
    mcp.disable(tags={"kitchen"})
    yield
    mcp._transforms.clear()
    mcp.disable(tags={"kitchen"})


@pytest.fixture()
def kitchen_enabled():
    """Enable the kitchen tag on the MCP server for the duration of the test."""
    from autoskillit.server import mcp

    mcp.enable(tags={"kitchen"})
    yield
    mcp.disable(tags={"kitchen"})


@pytest.fixture()
def headless_enabled():
    """Enable the headless tag on the MCP server for the duration of the test."""
    from autoskillit.server import mcp

    mcp.enable(tags={"headless"})
    yield
    mcp.disable(tags={"headless"})

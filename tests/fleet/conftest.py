"""Shared fixtures for tests/franchise/."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_tags():
    """Reset MCP tag visibility to default (kitchen + headless disabled) before each test.

    The mcp singleton is process-global. Tests that call mcp.enable(tags={"kitchen"})
    or mcp.enable(tags={"headless"}) mutate shared state. This fixture ensures every
    franchise test starts with all tags disabled — the same state as a fresh server import.
    """
    from autoskillit.server import mcp

    mcp.disable(tags={"kitchen"})
    mcp.disable(tags={"headless"})
    mcp.disable(tags={"fleet"})


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    """Reset module-level _ctx in server._state after each test.

    Tests that call _initialize() directly set _state._ctx to a mock without
    cleanup. monkeypatch records the current value before yield and restores it after,
    giving each test a clean slate regardless of what _initialize() sets.
    """
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", None)

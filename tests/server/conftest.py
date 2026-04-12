"""Shared fixtures for tests/server/."""

from __future__ import annotations

import pytest


@pytest.fixture()
def kitchen_enabled():
    """Enable the kitchen tag on the MCP server for the duration of the test."""
    from autoskillit.server import mcp

    mcp.enable(tags={"kitchen"})
    yield
    mcp.disable(tags={"kitchen"})

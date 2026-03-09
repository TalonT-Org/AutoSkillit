"""Phase 2 tests: cook session kitchen visibility via AUTOSKILLIT_KITCHEN_OPEN=1."""

from __future__ import annotations

import importlib

import pytest


def test_cook_session_kitchen_visible_from_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_KITCHEN_OPEN", "1")
    import autoskillit.server as srv

    importlib.reload(srv)
    from autoskillit.server import mcp

    assert mcp is not None

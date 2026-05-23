"""Tests for ProcessStaleError propagation through open_kitchen."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import ProcessStaleError
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_open_kitchen_stale_returns_failure_envelope():
    """ProcessStaleError from load_and_validate → failure envelope with staleness context."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.side_effect = ProcessStaleError(
        "Process is running stale code"
    )

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
    ):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert "stale" in parsed["error"].lower()
    assert parsed["stage"] == "process_stale"

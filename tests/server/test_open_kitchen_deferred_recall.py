"""Tests for active_recipe_steps assignment in the _is_deferred_recall=True path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_deferred_recall_ctx(name: str) -> MagicMock:
    ctx = _make_mock_ctx()
    ctx.gate.enabled = True
    ctx.recipe_name = name
    ctx.kitchen_id = "test-kitchen"
    return ctx


@pytest.mark.anyio
async def test_deferred_recall_sets_active_recipe_steps_from_recipe():
    """Deferred-recall path populates active_recipe_steps from the freshly loaded recipe."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info

    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"build": {"cmd": "task build"}, "test": {"cmd": "task test"}}
    mock_ctx.recipes.load.return_value = mock_recipe_obj

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert mock_ctx.active_recipe_steps == {
        "build": {"cmd": "task build"},
        "test": {"cmd": "task test"},
    }


@pytest.mark.anyio
async def test_deferred_recall_sets_active_recipe_steps_none_when_find_raises():
    """When recipes.find raises, active_recipe_steps is set to None and the call still succeeds."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
    }
    mock_ctx.recipes.find.side_effect = RuntimeError("disk error")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert mock_ctx.active_recipe_steps is None


@pytest.mark.anyio
async def test_deferred_recall_sets_active_recipe_steps_none_when_find_returns_none():
    """When recipes.find returns None (recipe not on disk), active_recipe_steps is None."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
    }
    mock_ctx.recipes.find.return_value = None

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert mock_ctx.active_recipe_steps is None

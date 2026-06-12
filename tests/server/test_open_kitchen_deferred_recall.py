"""Tests for the _is_deferred_recall=True path: active_recipe_steps and fail-closed guard."""

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
        "content": "name: test-recipe\nsteps:\n  build:\n    cmd: task build\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
        "suggestions": [],
        "post_prune_step_names": ["build", "test"],
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
        "content": "name: test-recipe\nsteps:\n  build:\n    cmd: task build\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
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
        "content": "name: test-recipe\nsteps:\n  build:\n    cmd: task build\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
    }
    mock_ctx.recipes.find.return_value = None

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert mock_ctx.active_recipe_steps is None


@pytest.mark.anyio
async def test_deferred_recall_fails_closed_when_valid_false_empty_content():
    """Guard fires when load_and_validate returns valid=False with empty content."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "",
        "valid": False,
        "errors": ["structural error A"],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
    }

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert parsed["stage"] == "recipe_validation"
    assert parsed["errors"] == ["structural error A"]
    assert "user_visible_message" in parsed


@pytest.mark.anyio
async def test_deferred_recall_fails_closed_when_valid_false_nonempty_content():
    """Guard fires on valid=False regardless of content presence."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "non-empty content",
        "valid": False,
        "errors": ["structural error B"],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
    }

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert parsed["stage"] == "recipe_validation"
    assert parsed["errors"] == ["structural error B"]
    assert "user_visible_message" in parsed


@pytest.mark.anyio
async def test_deferred_recall_fails_closed_when_valid_missing():
    """Guard treats absent valid key as False via result.get('valid', False)."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "non-empty content",
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
    }

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert parsed["stage"] == "recipe_validation"
    assert parsed["errors"] == []
    assert "user_visible_message" in parsed

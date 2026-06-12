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
    ctx.gate_infrastructure_ready = True
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


def _make_pre_revealed_ctx(name: str) -> MagicMock:
    ctx = _make_mock_ctx()
    ctx.gate.enabled = True
    ctx.recipe_name = ""
    ctx.kitchen_id = "test-kitchen"
    ctx.gate_infrastructure_ready = True
    ctx.recipes.load_and_validate.return_value = {
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
    return ctx


@pytest.mark.anyio
async def test_pre_reveal_then_open_does_not_re_execute_handler():
    """Pre-revealed state (gate enabled, recipe_name empty, infrastructure ready)
    must skip _open_kitchen_handler and still load the recipe."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_pre_revealed_ctx("test-recipe")
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"build": {"cmd": "task build"}}
    mock_recipe_obj.ingredients = {"ing1": "val1"}
    mock_ctx.recipes.load.return_value = mock_recipe_obj

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            tools_kitchen, "_open_kitchen_handler", new_callable=MagicMock
        ) as mock_handler,
    ):
        result = await tools_kitchen.open_kitchen(name="test-recipe", ctx=mock_ctx)

    mock_handler.assert_not_called()
    parsed = json.loads(result)
    assert parsed["success"] is True


@pytest.mark.anyio
async def test_deferred_recall_strips_content_when_ingredients_only_true():
    """Deferred-recall path must respect ingredients_only flag."""
    from autoskillit.server.tools import tools_kitchen

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
        "orchestration_rules": "some rules",
        "stop_step_semantics": "some semantics",
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"build": {"cmd": "task build"}}
    mock_recipe_obj.ingredients = {"ing1": "val1"}
    mock_ctx.recipes.load.return_value = mock_recipe_obj

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await tools_kitchen.open_kitchen(
            name="test-recipe", ingredients_only=True, ctx=mock_ctx
        )

    parsed = json.loads(result)
    assert "content" not in parsed
    assert "orchestration_rules" not in parsed
    assert "stop_step_semantics" not in parsed


@pytest.mark.anyio
async def test_double_open_kitchen_no_name_does_not_re_execute_handler():
    """Calling open_kitchen() with name=None while infrastructure is ready
    must not re-run _open_kitchen_handler."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_mock_ctx()
    mock_ctx.gate.enabled = True
    mock_ctx.gate_infrastructure_ready = True
    mock_ctx.kitchen_id = "test-kitchen"

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            tools_kitchen, "_open_kitchen_handler", new_callable=MagicMock
        ) as mock_handler,
    ):
        result = await tools_kitchen.open_kitchen(ctx=mock_ctx)

    mock_handler.assert_not_called()
    assert isinstance(result, str)


@pytest.mark.anyio
async def test_gate_rollback_resets_gate_infrastructure_ready():
    """When recipe validation fails in deferred-recall path, gate_infrastructure_ready
    must be reset so the next open_kitchen call re-runs the handler."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_pre_revealed_ctx("bad-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "",
        "valid": False,
        "errors": ["structural error"],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
    }

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await tools_kitchen.open_kitchen(name="bad-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert mock_ctx.gate_infrastructure_ready is False


@pytest.mark.anyio
async def test_pre_reveal_backend_does_not_support_tool_list_changed():
    """Simulates Codex pre-reveal: gate enabled, recipe_name empty, infrastructure ready.
    Handler must be skipped, recipe loaded normally."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_pre_revealed_ctx("test-recipe")
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"build": {"cmd": "task build"}}
    mock_recipe_obj.ingredients = {"ing1": "val1"}
    mock_ctx.recipes.load.return_value = mock_recipe_obj

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            tools_kitchen, "_open_kitchen_handler", new_callable=MagicMock
        ) as mock_handler,
    ):
        result = await tools_kitchen.open_kitchen(name="test-recipe", ctx=mock_ctx)

    mock_handler.assert_not_called()
    parsed = json.loads(result)
    assert parsed["success"] is True


@pytest.mark.anyio
async def test_cold_open_kitchen_runs_handler():
    """When gate_infrastructure_ready is False (cold state), handler must run."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_mock_ctx()
    mock_ctx.gate.enabled = False
    mock_ctx.gate_infrastructure_ready = False

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            tools_kitchen, "_open_kitchen_handler", new_callable=MagicMock
        ) as mock_handler,
    ):
        mock_handler.return_value = None
        result = await tools_kitchen.open_kitchen(name="test-recipe", ctx=mock_ctx)

    mock_handler.assert_called_once()
    assert isinstance(result, str)

"""Tests for MCP tool ingredient_overrides parameter propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server")]


def _make_mock_recipes(load_result: dict) -> MagicMock:
    """Create a mock recipe repository that returns the given load result."""
    mock = MagicMock()
    mock.load_and_validate.return_value = load_result
    mock.find.return_value = None
    mock.list_all.return_value = {"recipes": [], "count": 0}
    return mock


def _make_mock_ctx(recipes: MagicMock) -> MagicMock:
    mock_ctx = MagicMock()
    mock_ctx.recipes = recipes
    mock_ctx.config.migration.suppressed = []
    mock_ctx.gate.is_enabled.return_value = True
    return mock_ctx


async def test_load_recipe_tool_accepts_overrides_param(tmp_path: Path) -> None:
    """load_recipe MCP tool accepts ingredient_overrides dict and passes it through."""
    mock_recipes = _make_mock_recipes(
        {
            "content": "name: test\ndescription: test\n",
            "valid": True,
            "suggestions": [],
        }
    )
    mock_tool_ctx = _make_mock_ctx(mock_recipes)

    with (
        patch("autoskillit.server.tools_recipe._require_enabled", return_value=None),
        patch("autoskillit.server.tools_recipe._get_ctx_or_none", return_value=mock_tool_ctx),
        patch(
            "autoskillit.config.resolve_ingredient_defaults",
            return_value={},
        ),
        patch(
            "autoskillit.server.helpers._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
    ):
        from autoskillit.server.tools_recipe import load_recipe as _load_recipe_tool

        result_str = await _load_recipe_tool(name="test-recipe", overrides={"sprint_mode": "true"})
        result = json.loads(result_str)
        assert "error" not in result
        assert result.get("valid") is True

        # Verify overrides were passed through to load_and_validate
        mock_recipes.load_and_validate.assert_called_once()
        call_kwargs = mock_recipes.load_and_validate.call_args
        assert call_kwargs.kwargs.get("ingredient_overrides") == {"sprint_mode": "true"}


async def test_open_kitchen_accepts_overrides_param(tmp_path: Path) -> None:
    """open_kitchen MCP tool accepts overrides dict and passes it to load_and_validate."""
    mock_recipes = _make_mock_recipes(
        {
            "content": "name: test\ndescription: test\n",
            "valid": True,
            "suggestions": [],
        }
    )
    mock_tool_ctx = _make_mock_ctx(mock_recipes)

    mock_mcp_ctx = AsyncMock()
    mock_mcp_ctx.enable_components = AsyncMock()

    with (
        patch("autoskillit.server.tools_kitchen._require_not_headless", return_value=None),
        patch(
            "autoskillit.server.tools_kitchen._open_kitchen_handler",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("autoskillit.server._get_ctx", return_value=mock_tool_ctx),
        patch(
            "autoskillit.config.resolve_ingredient_defaults",
            return_value={},
        ),
        patch(
            "autoskillit.server.helpers._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
        patch("autoskillit.server.tools_kitchen.__version__", "0.0.0"),
    ):
        from autoskillit.server.tools_kitchen import open_kitchen as _open_kitchen_tool

        result_str = await _open_kitchen_tool(
            name="test-recipe",
            overrides={"sprint_mode": "true"},
            ctx=mock_mcp_ctx,
        )
        result = json.loads(result_str)
        assert result.get("kitchen") == "open"
        assert result.get("valid") is True

        # Verify overrides were passed through to load_and_validate
        mock_recipes.load_and_validate.assert_called_once()
        call_kwargs = mock_recipes.load_and_validate.call_args
        assert call_kwargs.kwargs.get("ingredient_overrides") == {"sprint_mode": "true"}


async def test_unknown_override_key_ignored(tmp_path: Path) -> None:
    """Overrides for undefined ingredients are silently ignored (no crash)."""
    from autoskillit.recipe._api import _build_active_recipe
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "task": RecipeIngredient(description="Task", required=True),
        },
        steps={
            "do_it": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi"},
                on_success="done",
                on_exhausted="escalate",
            )
        },
        kitchen_rules=["no native tools"],
    )
    # Override for an ingredient that doesn't exist
    active, combined = _build_active_recipe(recipe, {"nonexistent_key": "true"}, tmp_path)
    # Should not crash, recipe unchanged
    assert active is recipe
    assert combined is None

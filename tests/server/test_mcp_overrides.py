"""Tests for MCP tool ingredient_overrides parameter propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
        patch("autoskillit.server.tools.tools_recipe._require_enabled", return_value=None),
        patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none", return_value=mock_tool_ctx
        ),
        patch(
            "autoskillit.config.resolve_ingredient_defaults",
            return_value={},
        ),
        patch(
            "autoskillit.server._misc._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
    ):
        from autoskillit.server.tools.tools_recipe import load_recipe as _load_recipe_tool

        result_str = await _load_recipe_tool(
            name="test-recipe", overrides={"run_mode": "sequential"}
        )
        result = json.loads(result_str)
        assert "error" not in result
        assert result.get("valid") is True

        # Verify user-supplied overrides were passed through to load_and_validate
        # (merged with auto-injected kitchen_id and post_run_diagnostics)
        mock_recipes.load_and_validate.assert_called_once()
        call_kwargs = mock_recipes.load_and_validate.call_args
        actual_overrides = call_kwargs.kwargs.get("ingredient_overrides") or {}
        assert actual_overrides.get("run_mode") == "sequential", (
            "user-supplied override 'run_mode' missing from ingredient_overrides: "
            f"{actual_overrides}"
        )


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
        patch(
            "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact", return_value=None
        ),
        patch(
            "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("autoskillit.server._get_ctx", return_value=mock_tool_ctx),
        patch(
            "autoskillit.config.resolve_ingredient_defaults",
            return_value={},
        ),
        patch(
            "autoskillit.server._misc._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
        patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen as _open_kitchen_tool

        result_str = await _open_kitchen_tool(
            name="test-recipe",
            overrides={"run_mode": "sequential"},
            ctx=mock_mcp_ctx,
        )
        result = json.loads(result_str)
        assert result.get("kitchen") == "open"
        assert result.get("valid") is True

        # Verify user-supplied overrides were passed through to load_and_validate
        # (merged with auto-injected kitchen_id and post_run_diagnostics)
        mock_recipes.load_and_validate.assert_called_once()
        call_kwargs = mock_recipes.load_and_validate.call_args
        actual_overrides = call_kwargs.kwargs.get("ingredient_overrides") or {}
        assert actual_overrides.get("run_mode") == "sequential", (
            "user-supplied override 'run_mode' missing from ingredient_overrides: "
            f"{actual_overrides}"
        )


async def test_unknown_override_key_warned(tmp_path: Path) -> None:
    """open_kitchen warns about override keys that don't match declared ingredients."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

    mock_recipe = Recipe(
        name="test",
        description="test",
        ingredients={"audit": RecipeIngredient(description="audit gate", default="true")},
        steps={
            "audit_impl": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi"},
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=[],
    )
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "test.yaml"

    mock_recipes = _make_mock_recipes({"content": "name: test", "valid": True, "suggestions": []})
    mock_recipes.find.return_value = mock_recipe_info
    mock_recipes.load.return_value = mock_recipe

    mock_tool_ctx = _make_mock_ctx(mock_recipes)
    mock_mcp_ctx = AsyncMock()

    with (
        patch(
            "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact", return_value=None
        ),
        patch(
            "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("autoskillit.server._get_ctx", return_value=mock_tool_ctx),
        patch("autoskillit.config.resolve_ingredient_defaults", return_value={}),
        patch(
            "autoskillit.server._misc._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
        patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
        patch("autoskillit.server.tools.tools_kitchen._update_hook_config_with_recipe"),
        patch(
            "autoskillit.server.tools.tools_kitchen._build_hook_diagnostic_warning",
            return_value=None,
        ),
        patch("autoskillit.server._state._check_rerun", return_value=None),
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen as _open_kitchen_tool

        result_str = await _open_kitchen_tool(
            name="test",
            overrides={"audit_impl": "false"},  # "audit_impl" not a declared ingredient
            ctx=mock_mcp_ctx,
        )
    result = json.loads(result_str)
    assert result.get("kitchen") == "open"
    warnings = result.get("warnings", [])
    assert warnings, f"Expected warnings for unknown override key, got result: {result}"
    assert any("audit_impl" in w for w in warnings)


async def test_valid_override_key_no_warning(tmp_path: Path) -> None:
    """open_kitchen emits no warnings when all override keys match declared ingredients."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

    mock_recipe = Recipe(
        name="test",
        description="test",
        ingredients={"audit": RecipeIngredient(description="audit gate", default="true")},
        steps={
            "audit_impl": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi"},
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=[],
    )
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "test.yaml"

    mock_recipes = _make_mock_recipes({"content": "name: test", "valid": True, "suggestions": []})
    mock_recipes.find.return_value = mock_recipe_info
    mock_recipes.load.return_value = mock_recipe

    mock_tool_ctx = _make_mock_ctx(mock_recipes)
    mock_mcp_ctx = AsyncMock()

    with (
        patch(
            "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact", return_value=None
        ),
        patch(
            "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("autoskillit.server._get_ctx", return_value=mock_tool_ctx),
        patch("autoskillit.config.resolve_ingredient_defaults", return_value={}),
        patch(
            "autoskillit.server._misc._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
        patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
        patch("autoskillit.server.tools.tools_kitchen._update_hook_config_with_recipe"),
        patch(
            "autoskillit.server.tools.tools_kitchen._build_hook_diagnostic_warning",
            return_value=None,
        ),
        patch("autoskillit.server._state._check_rerun", return_value=None),
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen as _open_kitchen_tool

        result_str = await _open_kitchen_tool(
            name="test",
            overrides={"audit": "false"},  # "audit" IS a declared ingredient
            ctx=mock_mcp_ctx,
        )
    result = json.loads(result_str)
    assert result.get("kitchen") == "open"
    assert "warnings" not in result, f"Expected no warnings, got: {result.get('warnings')}"


async def test_unknown_override_key_warned_deferred_recall(tmp_path: Path) -> None:
    """open_kitchen deferred-recall path also warns about unknown override keys."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

    mock_recipe = Recipe(
        name="test",
        description="test",
        ingredients={"audit": RecipeIngredient(description="audit gate", default="true")},
        steps={
            "audit_impl": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi"},
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=[],
    )
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "test.yaml"

    mock_recipes = _make_mock_recipes({"content": "name: test", "valid": True, "suggestions": []})
    mock_recipes.find.return_value = mock_recipe_info
    mock_recipes.load.return_value = mock_recipe

    mock_tool_ctx = _make_mock_ctx(mock_recipes)
    # Simulate kitchen already open with recipe "test" loaded → deferred-recall path
    mock_tool_ctx.gate.enabled = True
    mock_tool_ctx.recipe_name = "test"
    mock_mcp_ctx = AsyncMock()

    with (
        patch(
            "autoskillit.server.tools.tools_kitchen._require_orchestrator_exact", return_value=None
        ),
        patch("autoskillit.server._get_ctx", return_value=mock_tool_ctx),
        patch("autoskillit.config.resolve_ingredient_defaults", return_value={}),
        patch(
            "autoskillit.server._misc._apply_triage_gate",
            new_callable=AsyncMock,
            return_value={"content": "test", "valid": True, "suggestions": []},
        ),
        patch("autoskillit.server.tools.tools_kitchen.__version__", "0.0.0"),
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen as _open_kitchen_tool

        result_str = await _open_kitchen_tool(
            name="test",
            overrides={"audit_impl": "false"},  # unknown key on deferred-recall path
            ctx=mock_mcp_ctx,
        )
    result = json.loads(result_str)
    assert result.get("kitchen") == "open"
    warnings = result.get("warnings", [])
    assert warnings, f"Expected warnings on deferred-recall path, got result: {result}"
    assert any("audit_impl" in w for w in warnings)

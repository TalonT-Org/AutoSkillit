"""Type gate enforcement tests for open_kitchen and load_recipe.

Verifies the Tier-2 gate: caller-supplied override values are coerced against
the recipe's declared ``RecipeIngredient.type`` and rejected with a structured
envelope on mismatch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.recipe.schema import RecipeIngredient
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _mock_recipe(ingredients: dict[str, RecipeIngredient]) -> SimpleNamespace:
    """Lightweight duck-typed recipe that exposes ``.ingredients``.

    The type gate only accesses ``recipe_obj.ingredients.get(key)`` and the
    resulting object's ``.type`` attribute, so a SimpleNamespace is sufficient.
    """
    ns = SimpleNamespace(ingredients=ingredients, steps={})
    return ns


def _patched_env(mock_ctx: MagicMock) -> None:
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-type"
    mock_ctx.config.linux_tracing.log_dir = ""


async def _call_open_kitchen_with_recipe(
    tmp_path, monkeypatch, recipe: SimpleNamespace, overrides: dict[str, str]
) -> str:
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)
    mock_ctx.recipes.load.return_value = recipe
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = "/fake/recipe.yaml"
    mock_ctx.recipes.find.return_value = mock_recipe_info

    # serve_recipe must succeed so the function reaches the type gate.
    from tests.server._helpers import _make_finalized_projection, _with_finalized_projection

    projection = _make_finalized_projection(
        ingredient_names=frozenset(recipe.ingredients.keys()),
    )
    serve_recipe_result = _with_finalized_projection(
        {
            "valid": True,
            "content": "name: demo\n",
            "errors": [],
            "warnings": [],
            "suggestions": [],
            "diagram": None,
            "ingredients_table": "--- TABLE ---",
            "requires_packs": [],
            "requires_features": [],
            "recipe_version": "",
        },
        projection=projection,
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-type",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={"base_branch": "develop"},
                        ):
                            with patch(
                                "autoskillit.server.tools.tools_kitchen.serve_recipe",
                                return_value=serve_recipe_result,
                            ):
                                from autoskillit.server.tools.tools_kitchen import open_kitchen

                                return await open_kitchen(
                                    name="demo",
                                    overrides=overrides,
                                    ctx=mock_ctx,
                                )


# ---------------------------------------------------------------------------
# Integer type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_rejects_invalid_integer_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"count": RecipeIngredient(description="Count", type="integer")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"count": "abc"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"
    assert "count" in parsed["error"]


@pytest.mark.anyio
async def test_open_kitchen_accepts_valid_integer_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"count": RecipeIngredient(description="Count", type="integer")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"count": "42"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Valid integer wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


# ---------------------------------------------------------------------------
# Boolean type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_rejects_invalid_boolean_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"flag": RecipeIngredient(description="Flag", type="boolean")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"flag": "maybe"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"
    assert "flag" in parsed["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["true", "false", "1", "0", "yes", "no"])
async def test_open_kitchen_accepts_each_boolean_value(tmp_path, monkeypatch, value):
    recipe = _mock_recipe({"flag": RecipeIngredient(description="Flag", type="boolean")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"flag": value}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Boolean value {value!r} wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


# ---------------------------------------------------------------------------
# List / dict JSON types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_rejects_invalid_list_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"items": RecipeIngredient(description="Items", type="list")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"items": "not-json"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"


@pytest.mark.anyio
async def test_open_kitchen_accepts_valid_list_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"items": RecipeIngredient(description="Items", type="list")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"items": "[1,2,3]"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Valid list JSON wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


@pytest.mark.anyio
async def test_open_kitchen_rejects_invalid_dict_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"config": RecipeIngredient(description="Config", type="dict")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"config": "not-json"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"


@pytest.mark.anyio
async def test_open_kitchen_accepts_valid_dict_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"config": RecipeIngredient(description="Config", type="dict")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"config": '{"a": 1}'}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Valid dict JSON wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


# ---------------------------------------------------------------------------
# Path types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_accepts_absolute_path_override(tmp_path, monkeypatch):
    recipe = _mock_recipe({"target": RecipeIngredient(description="Target", type="absolute_path")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"target": "/tmp/foo"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Valid absolute_path wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


@pytest.mark.anyio
async def test_open_kitchen_rejects_empty_absolute_path(tmp_path, monkeypatch):
    recipe = _mock_recipe({"target": RecipeIngredient(description="Target", type="absolute_path")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"target": ""}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"


# ---------------------------------------------------------------------------
# Untyped ingredient (skipped)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_skips_validation_for_untyped_ingredients(tmp_path, monkeypatch):
    """Caller override for a type=None ingredient is accepted without coercion."""
    recipe = _mock_recipe({"name": RecipeIngredient(description="Name")})
    result_str = await _call_open_kitchen_with_recipe(
        tmp_path, monkeypatch, recipe, {"name": "anything-here"}
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, f"Untyped ingredient wrongly rejected: {parsed}"
    assert parsed.get("stage") != "ingredient_type_validation"


# ---------------------------------------------------------------------------
# ingredients_only path also enforces type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_ingredients_only_rejects_invalid_type(tmp_path, monkeypatch):
    """ingredients_only=True path goes through _render_ingredients_only_response
    which has its own type gate."""
    monkeypatch.chdir(tmp_path)
    recipe = _mock_recipe({"count": RecipeIngredient(description="Count", type="integer")})
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)
    mock_ctx.recipes.load.return_value = recipe
    mock_ctx.recipes.find.return_value = MagicMock(path="/fake/recipe.yaml")

    from tests.server._helpers import _make_finalized_projection, _with_finalized_projection

    projection = _make_finalized_projection(
        ingredient_names=frozenset(recipe.ingredients.keys()),
    )
    serve_recipe_result = _with_finalized_projection(
        {
            "valid": True,
            "content": "name: demo\n",
            "errors": [],
            "warnings": [],
            "suggestions": [],
            "diagram": None,
            "ingredients_table": "--- TABLE ---",
            "requires_packs": [],
            "requires_features": [],
            "recipe_version": "",
        },
        projection=projection,
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-type",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={"base_branch": "develop"},
                        ):
                            with patch(
                                "autoskillit.server.tools.tools_kitchen.serve_recipe",
                                return_value=serve_recipe_result,
                            ):
                                from autoskillit.server.tools.tools_kitchen import open_kitchen

                                result_str = await open_kitchen(
                                    name="demo",
                                    overrides={"count": "abc"},
                                    ingredients_only=True,
                                    ctx=mock_ctx,
                                )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"

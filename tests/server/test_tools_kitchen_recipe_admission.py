"""Tests for open_kitchen recipe-admission control flow.

Covers the `name=` argument path: combining open with recipe admission,
rejecting unknown/skill names before operational mutation, and headless gate
denial for both open and close.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import RecipeNotFoundError
from autoskillit.pipeline import (
    bind_kitchen_intent,
    claim_kitchen_request,
    release_kitchen_request,
)
from tests.server._helpers import _configure_admitted_recipe, _with_finalized_projection
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_open_kitchen_with_recipe_returns_combined_response(tmp_path, monkeypatch):
    """open_kitchen(name='x') opens kitchen AND loads the recipe in one call."""
    monkeypatch.chdir(tmp_path)
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    yaml_content = (
        "name: test-recipe\ndescription: test\nsteps:\n  do:\n    tool: run_cmd\n"
        "    with:\n      cmd: echo hi\n    on_success: done\n    on_failure: done\n"
        "  done:\n    action: stop\n    message: Done\n"
    )
    (recipes_dir / "test-recipe.yaml").write_text(yaml_content)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": yaml_content,
        "valid": True,
        "suggestions": [],
        "diagram": None,
    }
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    _configure_admitted_recipe(mock_ctx, recipes_dir / "test-recipe.yaml")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is True
    assert result["kitchen"] == "open"
    assert "version" in result
    assert "content" in result
    assert "test-recipe" in result["content"]
    mock_ctx.gate.enable.assert_called_once()
    mock_ctx.enable_components.assert_called_once_with(tags={"kitchen"})


@pytest.mark.anyio
async def test_open_kitchen_reports_admitted_recipe_missing_during_load(tmp_path, monkeypatch):
    """An admitted recipe that disappears during loading fails closed."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    _configure_admitted_recipe(mock_ctx, tmp_path / "nonexistent.yaml")
    mock_ctx.recipes.load_and_validate.side_effect = RecipeNotFoundError(
        "No recipe named 'nonexistent' found"
    )
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="nonexistent", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is False
    assert result["stage"] == "load_and_validate"
    assert result["error"] == "RecipeNotFoundError: No recipe named 'nonexistent' found"
    assert result["kitchen"] == "failed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("recipe_name", "skill_match", "expected_message"),
    [
        ("dry-walkthrough", object(), "current session"),
        ("missing-recipe", None, "No recipe named 'missing-recipe' found"),
    ],
)
async def test_open_kitchen_rejects_non_recipe_before_operational_mutation(
    tmp_path,
    monkeypatch,
    recipe_name,
    skill_match,
    expected_message,
):
    """Skill-only and unknown names fail before operational state is mutated."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.find.return_value = None
    mock_ctx.skill_resolver.resolve_effective.return_value = skill_match
    mock_ctx.recipe_name = "existing-recipe"
    previous_execution = mock_ctx.recipe_initialization_state
    previous_quota_task = mock_ctx.quota_refresh_task

    handler = AsyncMock()
    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch("autoskillit.server.tools.tools_kitchen._open_kitchen_handler", handler),
        patch("autoskillit.server.tools.tools_kitchen.clear_recipe_execution") as clear,
        patch("autoskillit.server.tools.tools_kitchen.create_background_task") as create_task,
        patch(
            "autoskillit.server.tools.tools_kitchen.bind_kitchen_intent",
            wraps=bind_kitchen_intent,
        ) as bind_request,
        patch(
            "autoskillit.server.tools.tools_kitchen.claim_kitchen_request",
            wraps=claim_kitchen_request,
        ) as claim_request,
        patch(
            "autoskillit.server.tools.tools_kitchen.release_kitchen_request",
            wraps=release_kitchen_request,
        ) as release_request,
        patch("autoskillit.server.tools.tools_kitchen.mcp") as mock_mcp,
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        result = json.loads(await open_kitchen(name=recipe_name, ctx=mock_ctx))

    assert result["stage"] == "recipe_namespace"
    assert expected_message in result["user_visible_message"]
    handler.assert_not_awaited()
    clear.assert_not_called()
    create_task.assert_not_called()
    mock_mcp.enable.assert_not_called()
    mock_ctx.recipes.load.assert_not_called()
    mock_ctx.recipes.load_and_validate.assert_not_called()
    bind_request.assert_called_once()
    claim_request.assert_called_once()
    release_request.assert_called_once()
    assert mock_ctx.recipe_name == "existing-recipe"
    assert mock_ctx.recipe_initialization_state is previous_execution
    assert mock_ctx.quota_refresh_task is previous_quota_task
    assert mock_ctx.kitchen_open_state.phase.value == "request_bound"
    assert mock_ctx.kitchen_open_state.request_active is False
    effects = [(effect.name, effect.phase.value) for effect in mock_ctx.kitchen_open_state.effects]
    assert effects == [("request_binding", "confirmed")]


@pytest.mark.anyio
async def test_open_kitchen_does_not_misclassify_resolver_runtime_error(tmp_path, monkeypatch):
    """A resolver failure is not reported as missing server configuration."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.find.return_value = None
    mock_ctx.skill_resolver.resolve_effective.side_effect = RuntimeError("resolver failed")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        result = json.loads(await open_kitchen(name="broken-recipe", ctx=mock_ctx))

    assert result["stage"] == "unhandled"
    assert result["error"] == "RuntimeError: resolver failed"


@pytest.mark.anyio
async def test_open_kitchen_without_recipe_bypasses_namespace_admission(tmp_path, monkeypatch):
    """open_kitchen(name=None) activates without consulting recipe or skill names."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes.find.side_effect = AssertionError("anonymous activation admitted a recipe")
    mock_ctx.skill_resolver.resolve_effective.side_effect = AssertionError(
        "anonymous activation resolved a skill"
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "Kitchen is open" in parsed["content"]
    assert parsed["ingredients_table"] is None
    mock_ctx.recipes.find.assert_not_called()
    mock_ctx.skill_resolver.resolve_effective.assert_not_called()


@pytest.mark.anyio
async def test_open_kitchen_denied_by_gate_when_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    result = json.loads(await open_kitchen())
    assert result["success"] is False
    assert result["kitchen"] == "failed"
    assert "user_visible_message" in result
    assert len(result["user_visible_message"]) > 0
    assert result["stage"] == "headless_guard"


@pytest.mark.anyio
async def test_close_kitchen_denied_when_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    result = json.loads(await close_kitchen())
    assert result["success"] is False
    assert result["subtype"] == "headless_error"

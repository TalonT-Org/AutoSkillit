"""Tests for tools_kitchen.py: recipe packs, quota refresh, ingredients_only, project_dir."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# REQ-PACK-008: open_kitchen stores active_recipe_packs
@pytest.mark.anyio
async def test_open_kitchen_sets_active_recipe_packs(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), ctx.active_recipe_packs is frozenset()."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    assert mock_ctx.active_recipe_packs == frozenset()


# REQ-PACK-008: close_kitchen clears active_recipe_packs
def test_close_kitchen_clears_active_recipe_packs(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), ctx.active_recipe_packs is None."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.active_recipe_packs = frozenset(["research"])

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert mock_ctx.active_recipe_packs is None


# T-REFRESH-1
@pytest.mark.anyio
async def test_open_kitchen_starts_quota_refresh_task(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), ctx.quota_refresh_task is a running asyncio.Task."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    async def instant_loop(config, *, provider="anthropic"):
        await asyncio.sleep(0)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", AsyncMock()):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._quota_refresh_loop", instant_loop
                    ):
                        from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                        await _open_kitchen_handler()

    assert mock_ctx.quota_refresh_task is not None
    assert isinstance(mock_ctx.quota_refresh_task, asyncio.Task)
    mock_ctx.quota_refresh_task.cancel()


# T-REFRESH-2
def test_close_kitchen_cancels_quota_refresh_task(tmp_path, monkeypatch):
    """_close_kitchen_handler cancels ctx.quota_refresh_task and sets it to None."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_task = MagicMock(spec=asyncio.Task)
    mock_ctx.quota_refresh_task = mock_task

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    mock_task.cancel.assert_called_once()
    assert mock_ctx.quota_refresh_task is None


# T-REFRESH-3
def test_tool_context_has_quota_refresh_task_field():
    """ToolContext must have a quota_refresh_task field defaulting to None."""
    from autoskillit.pipeline.context import ToolContext

    fields = {f.name: f for f in dataclasses.fields(ToolContext)}
    assert "quota_refresh_task" in fields
    assert fields["quota_refresh_task"].default is None


# ---------------------------------------------------------------------------
# Group K — ingredients_only parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_ingredients_only_strips_content(tmp_path, monkeypatch):
    """open_kitchen(name=X, ingredients_only=True) must omit content from result."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: test\nsteps:\n  s1:\n    tool: run_skill\n",
        "ingredients_table": (
            "| Name | Description | Default |\n| task | What to do | (required) |"
        ),
        "suggestions": [],
        "valid": True,
        "orchestration_rules": "MANDATORY: execute every step",
        "stop_step_semantics": "stop semantics text",
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
    }
    mock_ctx.recipes.find.return_value = MagicMock()
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                        new=AsyncMock(side_effect=lambda r, *a, **kw: r),
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        result_json = await open_kitchen(
                            name="test", ingredients_only=True, ctx=mock_ctx
                        )

    result = json.loads(result_json)
    assert result["success"] is True
    assert result["ingredients_table"] is not None
    assert "content" not in result
    assert "orchestration_rules" not in result
    assert "sous_chef_discipline" not in result
    assert "stop_step_semantics" not in result


@pytest.mark.anyio
async def test_open_kitchen_ingredients_only_preserves_metadata(tmp_path, monkeypatch):
    """ingredients_only=True must preserve success, kitchen, version, valid, suggestions."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: test\nsteps:\n  s1:\n    tool: run_skill\n",
        "ingredients_table": "| Name | Description | Default |",
        "suggestions": [],
        "valid": True,
        "content_hash": "abc123",
        "composite_hash": "def456",
        "recipe_version": "1.0",
    }
    mock_ctx.recipes.find.return_value = MagicMock()
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                        new=AsyncMock(side_effect=lambda r, *a, **kw: r),
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        result_json = await open_kitchen(
                            name="test", ingredients_only=True, ctx=mock_ctx
                        )

    result = json.loads(result_json)
    assert result["success"] is True
    assert result["kitchen"] == "open"
    assert "version" in result
    assert result["valid"] is True
    assert result["suggestions"] == []


@pytest.mark.anyio
async def test_open_kitchen_ingredients_only_no_name_ignored(tmp_path, monkeypatch):
    """ingredients_only=True with name=None should behave like regular no-name open."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_json = await open_kitchen(ingredients_only=True, ctx=mock_ctx)

    result = json.loads(result_json)
    assert result["success"] is True
    assert "content" in result
    assert result["kitchen"] == "open"
    assert "version" in result


@pytest.mark.anyio
async def test_open_kitchen_uses_project_dir_for_recipe_lookup(tmp_path, monkeypatch):
    """open_kitchen must use tool_ctx.project_dir for recipe discovery, not Path.cwd().

    Regression test: when project_dir differs from cwd, recipes must be found
    in project_dir's .autoskillit/recipes/ directory.
    """
    monkeypatch.chdir(tmp_path)  # Ensure cwd != project_dir
    different_dir = tmp_path / "project_root"
    different_dir.mkdir()

    # Create a recipe only in project_dir, not in cwd
    recipe_yaml_text = (
        "name: test-project-dir-recipe\n"
        "description: Test recipe for project_dir propagation\n"
        "autoskillit_version: '0.2.0'\n"
        "kitchen_rules:\n"
        "  - Never use native tools\n"
        "steps:\n"
        "  stop:\n"
        "    action: stop\n"
        "    message: done\n"
    )
    recipes_dir = different_dir / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "test-project-dir-recipe.yaml").write_text(recipe_yaml_text)

    # Build context with project_dir = different_dir, recipes = RealRecipeRepository
    from autoskillit.config.settings import AutomationConfig
    from autoskillit.core.types import DirectInstall
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog
    from autoskillit.recipe.repository import DefaultRecipeRepository

    real_repo = DefaultRecipeRepository()

    ctx = ToolContext(
        config=AutomationConfig(features={"fleet": True}),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=False),
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=different_dir,
        recipes=real_repo,
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.gate = ctx.gate
    mock_ctx.recipes = ctx.recipes
    mock_ctx.config = ctx.config
    mock_ctx.project_dir = ctx.project_dir
    mock_ctx.active_recipe_packs = frozenset()
    mock_ctx.active_recipe_features = frozenset()
    mock_ctx.quota_refresh_task = None

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch(
                    "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                    new=AsyncMock(side_effect=lambda r, *a, **kw: r),
                ):
                    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen._build_hook_diagnostic_warning",
                            return_value="",
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_json = await open_kitchen(
                                name="test-project-dir-recipe", ctx=mock_ctx
                            )

    result = json.loads(result_json)
    assert result["success"] is True, (
        f"open_kitchen failed to find recipe in project_dir={different_dir}. Result: {result}"
    )
    assert result.get("kitchen") == "open"


def test_get_recipe_uses_project_dir(tmp_path, monkeypatch):
    """get_recipe must use ctx.project_dir for recipe lookup, not Path.cwd().

    Regression test: when project_dir differs from cwd, get_recipe must
    find recipes in project_dir's .autoskillit/recipes/ directory.
    """
    import yaml

    monkeypatch.chdir(tmp_path)
    different_dir = tmp_path / "project_root"
    different_dir.mkdir()

    recipe_yaml = {
        "name": "test-get-recipe-project-dir",
        "description": "Test recipe for get_recipe project_dir",
        "steps": {"s1": {"tool": "run_skill"}},
    }
    recipes_dir = different_dir / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "test-get-recipe-project-dir.yaml").write_text(yaml.dump(recipe_yaml))

    from autoskillit.recipe.repository import DefaultRecipeRepository

    real_repo = DefaultRecipeRepository()

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = different_dir
    mock_ctx.recipes = real_repo

    with patch("autoskillit.server._state._get_ctx_or_none", return_value=mock_ctx):
        from autoskillit.server.tools.tools_kitchen import get_recipe

        result = get_recipe("test-get-recipe-project-dir")

    # get_recipe returns raw YAML when found, JSON error dict when not found
    assert '"error"' not in result, (
        f"get_recipe failed to find recipe in project_dir={different_dir}. Result: {result}"
    )
    assert "test-get-recipe-project-dir" in result, (
        f"get_recipe did not return the recipe from project_dir. Result: {result}"
    )

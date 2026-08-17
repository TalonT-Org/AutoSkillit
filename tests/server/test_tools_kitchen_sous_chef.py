"""Tests for sous-chef discipline injection in open_kitchen (Group H).

Path B (no-name) injects SOUS_CHEF_MANDATORY_SECTIONS into the response content.
Path A (name=) does NOT inject discipline — delivery is via the food-truck
dispatch block in fleet/_prompts.py.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core.types._type_constants import SOUS_CHEF_MANDATORY_SECTIONS
from tests.server._helpers import _configure_admitted_recipe, _with_finalized_projection
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_sous_chef_discipline_not_in_open_kitchen_result(tmp_path, monkeypatch):
    """open_kitchen does not inject sous_chef_discipline — delivery is via system prompt."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: implementation\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
    }
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    _configure_admitted_recipe(mock_ctx, tmp_path / "implementation.yaml")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is True
    assert "sous_chef_discipline" not in result, (
        "sous_chef_discipline must not be injected by open_kitchen; "
        "food truck delivery is handled by _build_admiral_dispatch_block() "
        "in fleet/_prompts.py"
    )


@pytest.mark.anyio
async def test_open_kitchen_result_keys_match_typed_dict(tmp_path, monkeypatch):
    """All keys in open_kitchen result are declared in OpenKitchenResult."""
    from autoskillit.recipe._recipe_ingredients import OpenKitchenResult

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: implementation\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
    }
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    _configure_admitted_recipe(mock_ctx, tmp_path / "implementation.yaml")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    declared = set(OpenKitchenResult.__annotations__)
    undeclared = set(result.keys()) - declared
    assert undeclared == set(), (
        f"open_kitchen returned keys not declared in OpenKitchenResult: {sorted(undeclared)}. "
        "Add each to OpenKitchenResult in recipe/_recipe_ingredients.py."
    )
    for key in ("success", "kitchen", "version"):
        assert key in result, (
            f"open_kitchen result missing always-present key {key!r}. "
            "OpenKitchenResult declares it but the handler does not populate it."
        )


@pytest.mark.anyio
async def test_sous_chef_rules_injected_at_open_kitchen(tmp_path, monkeypatch):
    """Path B (no-name) must inject full sous-chef SKILL.md into response text."""
    from autoskillit.execution.backends import ClaudeCodeBackend
    from autoskillit.workspace import SkillsDirectoryProvider

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.project_dir = tmp_path
    mock_ctx.skill_resolver = SkillsDirectoryProvider().resolver
    mock_ctx.backend = ClaudeCodeBackend()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    content = parsed["content"]
    for header in SOUS_CHEF_MANDATORY_SECTIONS:
        assert header in content, (
            f"open_kitchen no-name response missing sous-chef section: {header!r}"
        )


@pytest.mark.anyio
async def test_open_kitchen_degrades_gracefully_without_sous_chef(tmp_path, monkeypatch):
    """When sous-chef SKILL.md is absent, Path B must return a valid response without raising."""
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.skill_resolver.list_effective.return_value = SimpleNamespace(skills=())
    mock_ctx.skill_resolver.resolve_effective.return_value = None

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True

"""Dispatch identity admission tests for recipe-serving tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import autoskillit.server.tools.tools_kitchen as _patch_tools_kitchen
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_POST_GATE = "reached-post-gate"


def _set_session_shape(
    monkeypatch: pytest.MonkeyPatch,
    *,
    headless: str,
    dispatch_id: str,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", headless)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    if dispatch_id:
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", dispatch_id)
    else:
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)


def _make_recipe_ctx() -> MagicMock:
    mock_ctx = _make_mock_ctx()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load.return_value = SimpleNamespace(steps={})
    mock_ctx.backend = None
    mock_ctx.config.migration.suppressed = []
    return mock_ctx


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("headless", "dispatch_id", "expect_denied"),
    [
        ("1", "", True),
        ("1", "d-123", False),
        ("0", "", False),
    ],
    ids=["headless-orchestrator-missing-id", "headless-orchestrator-id", "interactive"],
)
async def test_open_kitchen_requires_dispatch_identity_for_headless_orchestrator(
    tmp_path,
    monkeypatch,
    headless: str,
    dispatch_id: str,
    expect_denied: bool,
) -> None:
    """Only a headless orchestrator without an ID is denied before loading."""
    monkeypatch.chdir(tmp_path)
    _set_session_shape(monkeypatch, headless=headless, dispatch_id=dispatch_id)
    mock_ctx = _make_recipe_ctx()
    mock_handler = AsyncMock(return_value=_POST_GATE)

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            _patch_tools_kitchen,
            "_open_kitchen_handler",
            new=mock_handler,
        ),
    ):
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        if expect_denied:
            result = await open_kitchen(name="demo", ctx=mock_ctx)
        else:
            result = await open_kitchen(ctx=mock_ctx)

    if expect_denied:
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["stage"] == "preflight:dispatch_identity"
        assert parsed["retriable"] is False
        assert "AUTOSKILLIT_DISPATCH_ID" in parsed["error"]
        assert "MCP server process" in parsed["error"]
        mock_ctx.recipes.load.assert_not_called()
        mock_handler.assert_not_awaited()
    else:
        assert result == _POST_GATE
        mock_handler.assert_awaited_once()


@pytest.mark.anyio
async def test_open_kitchen_authority_denial_precedes_dispatch_identity(
    tmp_path, monkeypatch
) -> None:
    """Authority validation remains the first tool-local admission gate."""
    monkeypatch.chdir(tmp_path)
    _set_session_shape(monkeypatch, headless="1", dispatch_id="")
    mock_ctx = _make_recipe_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        result = await open_kitchen(
            name="demo",
            overrides={"dispatch_id": "caller-supplied"},
            ctx=mock_ctx,
        )

    parsed = json.loads(result)
    assert parsed["stage"] == "ingredient_authority_validation"
    mock_ctx.recipes.load.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("headless", "dispatch_id", "expect_denied"),
    [
        ("1", "", True),
        ("1", "d-123", False),
        ("0", "", False),
    ],
    ids=["headless-orchestrator-missing-id", "headless-orchestrator-id", "interactive"],
)
async def test_load_recipe_requires_dispatch_identity_for_headless_orchestrator(
    tmp_path,
    monkeypatch,
    headless: str,
    dispatch_id: str,
    expect_denied: bool,
) -> None:
    """load_recipe applies the same identity boundary before repository access."""
    monkeypatch.chdir(tmp_path)
    _set_session_shape(monkeypatch, headless=headless, dispatch_id=dispatch_id)
    mock_ctx = _make_recipe_ctx()
    mock_serve = MagicMock(return_value={"valid": False})

    from autoskillit.server.tools import tools_recipe

    with (
        patch.object(tools_recipe, "_require_enabled", return_value=None),
        patch.object(tools_recipe, "_get_ctx_or_none", return_value=mock_ctx),
        patch.object(tools_recipe, "resolve_ingredient_defaults", return_value={}),
        patch.object(
            tools_recipe,
            "_admit_recipe_name",
            return_value=SimpleNamespace(path="/fake/recipe.yaml"),
        ),
        patch.object(tools_recipe, "_compute_effective_backend_map", return_value=({}, {})),
        patch.object(tools_recipe, "build_backend_capabilities_map", return_value={}),
        patch.object(tools_recipe, "serve_recipe", mock_serve),
        patch.object(
            tools_recipe,
            "_finalize_load_recipe_result",
            new=AsyncMock(return_value=_POST_GATE),
        ),
    ):
        result = await tools_recipe.load_recipe(name="demo")

    if expect_denied:
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["stage"] == "preflight:dispatch_identity"
        assert parsed["retriable"] is False
        assert "AUTOSKILLIT_DISPATCH_ID" in parsed["error"]
        assert "MCP server process" in parsed["error"]
        mock_ctx.recipes.load.assert_not_called()
        mock_serve.assert_not_called()
    else:
        assert result == _POST_GATE
        mock_ctx.recipes.load.assert_called_once_with("/fake/recipe.yaml")
        mock_serve.assert_called_once()


@pytest.mark.anyio
async def test_load_recipe_authority_denial_precedes_dispatch_identity(
    tmp_path, monkeypatch
) -> None:
    """load_recipe preserves the Tier-1 authority check before identity admission."""
    monkeypatch.chdir(tmp_path)
    _set_session_shape(monkeypatch, headless="1", dispatch_id="")
    mock_ctx = _make_recipe_ctx()

    from autoskillit.server.tools import tools_recipe

    with (
        patch.object(tools_recipe, "_require_enabled", return_value=None),
        patch.object(tools_recipe, "_get_ctx_or_none", return_value=mock_ctx),
    ):
        result = await tools_recipe.load_recipe(
            name="demo",
            overrides={"dispatch_id": "caller-supplied"},
        )

    parsed = json.loads(result)
    assert parsed["stage"] == "ingredient_authority_validation"
    mock_ctx.recipes.load.assert_not_called()

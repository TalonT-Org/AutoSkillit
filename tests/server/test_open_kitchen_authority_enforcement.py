"""Authority gate enforcement tests for open_kitchen and load_recipe.

Verifies the Tier-1 gate: caller-supplied overrides for
``SERVER_AUTHORITATIVE_INGREDIENTS`` keys are rejected with a structured
envelope before any side effect (recipe load, serve_recipe, session snapshot
mutation).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.config.ingredient_defaults import SERVER_AUTHORITATIVE_INGREDIENTS
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _patched_env(mock_ctx: MagicMock) -> None:
    """Wire the minimum mock_ctx patches the gate needs (everything after the
    gate is gated by it; the gate itself runs at function entry)."""
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-auth"
    mock_ctx.config.linux_tracing.log_dir = ""


@pytest.mark.anyio
@pytest.mark.parametrize("authority_key", sorted(SERVER_AUTHORITATIVE_INGREDIENTS))
async def test_open_kitchen_rejects_each_server_authoritative_ingredient(
    authority_key, tmp_path, monkeypatch
):
    """Every SERVER_AUTHORITATIVE_INGREDIENTS key triggers rejection."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={authority_key: "anything"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert parsed["retriable"] is False
    assert authority_key in parsed["error"]


@pytest.mark.anyio
async def test_open_kitchen_rejects_base_branch_override(tmp_path, monkeypatch):
    """Direct test for base_branch — the most common authority override."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"base_branch": "main"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert "base_branch" in parsed["error"]
    assert "branching.default_base_branch" in parsed["user_visible_message"]


@pytest.mark.anyio
async def test_open_kitchen_still_accepts_non_authoritative_overrides(tmp_path, monkeypatch):
    """Regression: non-authoritative overrides are NOT rejected by the gate."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = "/fake/recipe.yaml"
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_ctx.recipes.load.return_value = MagicMock(
        steps={"do": MagicMock()}, ingredients={"audit": MagicMock(spec=["type"], type=None)}
    )
    # Provide enough mocking for the success path past the gate.
    mock_ctx.recipes.load_and_validate.side_effect = lambda *_args, **_kwargs: {  # noqa: ARG005
        "content": "name: demo\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "--- TABLE ---",
    }

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={"base_branch": "develop"},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            # Pass a non-authoritative override — gate must NOT fire.
                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"audit": "false"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    # The gate must NOT have rejected this call. Either the success path returns
    # a valid response or some downstream failure occurs — what matters is the
    # envelope does NOT have stage=ingredient_authority_validation. Note that a
    # downstream ``recipe_validation`` failure is acceptable here; the gate ran
    # first and let the call through.
    assert parsed.get("stage") != "ingredient_authority_validation", (
        f"Non-authoritative override wrongly rejected: {parsed}"
    )


@pytest.mark.anyio
async def test_open_kitchen_authority_supersedes_type_validation(tmp_path, monkeypatch):
    """Authority gate runs BEFORE type validation. Caller passing both an
    authority key and a typed-invalid value gets the authority error, not the
    type error."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)
    # Configure a recipe with ``count: RecipeIngredient(type=integer)`` so the
    # type gate WOULD fire if it ran before the authority gate.
    typed_recipe = MagicMock(
        ingredients={"count": MagicMock(spec=["type"], type="integer")},
        steps={"do": MagicMock()},
    )
    mock_ctx.recipes.load.return_value = typed_recipe
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = "/fake/recipe.yaml"
    mock_ctx.recipes.find.return_value = mock_recipe_info

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={
                                    "base_branch": "x",
                                    "count": "abc",  # would fail type=integer
                                },
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    # Authority must win, not type.
    assert parsed["stage"] == "ingredient_authority_validation", (
        f"Authority gate must run before type gate; got stage={parsed.get('stage')!r}"
    )


@pytest.mark.anyio
async def test_open_kitchen_no_snapshot_persisted_on_rejection(tmp_path, monkeypatch):
    """Regression for snapshot side-effect leak: on rejection, the kitchen's
    session_serve_overrides is NOT touched."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)
    sentinel_overrides = {"audit": "false"}
    mock_ctx.session_serve_overrides = sentinel_overrides
    mock_ctx.session_serve_defer_unresolved = False

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"base_branch": "main"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    # Snapshot must be unchanged. Asserting identity AND equality catches both
    # full reassignment and in-place mutation (e.g. ``dict.update``).
    assert mock_ctx.session_serve_overrides is sentinel_overrides, (
        "session_serve_overrides reassigned on authority rejection — gate ran AFTER "
        "snapshot write instead of BEFORE."
    )
    assert mock_ctx.session_serve_overrides == {"audit": "false"}, (
        "session_serve_overrides mutated in place on authority rejection — "
        "gate ran AFTER snapshot write instead of BEFORE."
    )


@pytest.mark.anyio
async def test_open_kitchen_ingredients_only_rejects_authority_override(tmp_path, monkeypatch):
    """ingredients_only=True path also rejects authority overrides (the gate
    runs at function entry, before any branching)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    _patched_env(mock_ctx)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=AsyncMock(),
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-auth",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={},
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"base_branch": "main"},
                                ingredients_only=True,
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"


@pytest.mark.anyio
async def test_load_recipe_rejects_config_authority_override(tool_ctx_kitchen_open):
    """load_recipe mirrors open_kitchen's authority gate."""
    from autoskillit.server.tools.tools_recipe import load_recipe

    tool_ctx_kitchen_open.recipes = MagicMock()

    with patch(
        "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
        return_value=tool_ctx_kitchen_open,
    ):
        result_str = await load_recipe(
            name="demo",
            overrides={"base_branch": "main"},
        )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert "base_branch" in parsed["error"]
    tool_ctx_kitchen_open.recipes.load.assert_not_called()


@pytest.mark.anyio
async def test_load_recipe_ingredients_only_rejects_authority_override(tool_ctx_kitchen_open):
    """load_recipe's ingredients_only=True path also rejects."""
    from autoskillit.server.tools.tools_recipe import load_recipe

    tool_ctx_kitchen_open.recipes = MagicMock()

    with patch(
        "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
        return_value=tool_ctx_kitchen_open,
    ):
        result_str = await load_recipe(
            name="demo",
            overrides={"base_branch": "main"},
            ingredients_only=True,
        )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    tool_ctx_kitchen_open.recipes.load.assert_not_called()


def test_authority_rejection_envelope_shape():
    """The rejection envelope from build_authority_rejection_envelope has the
    expected field shape: success=False, error mentions key, stage=
    ingredient_authority_validation, retriable=False, user_visible_message
    non-empty."""
    from autoskillit.server.tools._authority_feedback import (
        build_authority_rejection_envelope,
    )

    env = build_authority_rejection_envelope({"base_branch", "dispatch_id"})
    assert env["success"] is False
    assert isinstance(env["error"], str)
    assert "base_branch" in env["error"]
    assert "dispatch_id" in env["error"]
    assert env["stage"] == "ingredient_authority_validation"
    assert env["retriable"] is False
    assert isinstance(env["user_visible_message"], str)
    assert len(env["user_visible_message"]) > 0

"""Tests for tools_kitchen.py: recipe-validation errors and pipeline-health demotion."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_recipe_validation_error_response_surfaces_semantic_errors():
    """_recipe_validation_error_response must merge error-severity suggestions
    into the user-visible message when errors is empty."""
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "valid": False,
        "errors": [],
        "suggestions": [
            {
                "severity": "error",
                "rule": "backend-incompatible-skill",
                "message": "Step 'deploy' uses skill 'merge_worktree'",
                "step": "deploy",
            },
        ],
    }
    response = json.loads(_recipe_validation_error_response("demo", result))
    assert response["success"] is False
    assert response["kitchen"] == "failed"
    assert response["stage"] == "recipe_validation"
    assert "unknown structural error" not in response["user_visible_message"]
    assert "backend-incompatible-skill" in response["user_visible_message"]
    assert "merge_worktree" in response["error"]


@pytest.mark.parametrize(
    "result,expected_substring",
    [
        (
            {"valid": False, "errors": ["schema violation"], "suggestions": []},
            "schema violation",
        ),
        (
            {
                "valid": False,
                "errors": [],
                "suggestions": [
                    {
                        "severity": "error",
                        "rule": "rule-x",
                        "message": "bad step",
                        "step": "s",
                    }
                ],
            },
            "rule-x",
        ),
        (
            {
                "valid": False,
                "errors": [],
                "suggestions": [
                    {
                        "severity": "error",
                        "rule": "contract-y",
                        "message": "stale",
                        "step": "c",
                    }
                ],
            },
            "contract-y",
        ),
    ],
)
def test_validation_error_envelope_always_names_cause(result, expected_substring):
    """Envelope must always name a cause when valid=False, never 'unknown structural error'."""
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    response = json.loads(_recipe_validation_error_response("demo", result))
    assert response["success"] is False
    assert response["kitchen"] == "failed"
    assert response["stage"] == "recipe_validation"
    assert "unknown structural error" not in response["user_visible_message"]
    assert expected_substring in response["user_visible_message"]


@pytest.mark.anyio
async def test_open_kitchen_fails_on_semantic_errors_only(tmp_path, monkeypatch):
    """open_kitchen must surface semantic error findings when errors=[]."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": False,
        "errors": [],
        "suggestions": [
            {
                "severity": "error",
                "rule": "backend-incompatible-skill",
                "message": "Step 'deploy' uses skill 'merge_worktree'",
                "step": "deploy",
            },
        ],
    }
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert "unknown structural error" not in parsed["user_visible_message"]
    assert "backend-incompatible-skill" in parsed["user_visible_message"]


def test_recipe_validation_error_response_handles_malformed_suggestions():
    """_recipe_validation_error_response must not crash on suggestions missing rule/message.

    Regression contract: a suggestion dict that omits `rule` and `message` must
    still produce a structured error response (success=False) that surfaces the
    rule-tagged fallback (`[unknown-rule]` in place of the missing rule name)
    rather than a generic "unknown structural error" placeholder or a crash.
    """
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "valid": False,
        "errors": [],
        "suggestions": [
            {"severity": "error", "step": "some-step"},
        ],
    }
    response = json.loads(_recipe_validation_error_response("demo", result))
    assert "unknown structural error" not in response["user_visible_message"]
    assert response["success"] is False
    # Fallback contract: the unknown-rule placeholder is emitted in place of the
    # missing rule/message so the user-visible message still names the failing
    # suggestion rather than collapsing to a generic error.
    assert "[unknown-rule]" in response["user_visible_message"]


# ---------------------------------------------------------------------------
# pipeline_health demotion tests (T1, T2, T5)
# ---------------------------------------------------------------------------


# T1: REQ-ING-001
@pytest.mark.anyio
async def test_pipeline_health_override_wins_over_config(tmp_path, monkeypatch):
    """REQ-ING-001: open_kitchen override for pipeline_health wins over config."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"do": MagicMock()}
    mock_recipe_obj.ingredients = {"pipeline_health": MagicMock()}
    mock_ctx.recipes.load.return_value = mock_recipe_obj
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "--- TABLE ---",
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"
    mock_ctx.config.linux_tracing.log_dir = ""

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={
                                "base_branch": "develop",
                                "pipeline_health": "false",
                                "is_fleet_dispatch": "false",
                                "dispatch_id": "",
                            },
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            await open_kitchen(
                                name="demo",
                                overrides={"pipeline_health": "true"},
                                ctx=mock_ctx,
                            )

    call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
    overrides = call_kwargs["ingredient_overrides"]
    assert overrides["pipeline_health"] == "true", f"Override must win; got overrides={overrides}"
    call_args_list = mock_ctx.recipes.load_and_validate.call_args_list
    assert call_args_list, "open_kitchen should call load_and_validate"


# T2: REQ-ING-002
@pytest.mark.anyio
async def test_pipeline_health_config_default_applied(tmp_path, monkeypatch):
    """REQ-ING-002: Without override, config value is used as default."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"do": MagicMock()}
    mock_recipe_obj.ingredients = {"pipeline_health": MagicMock()}
    mock_ctx.recipes.load.return_value = mock_recipe_obj
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "--- TABLE ---",
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"
    mock_ctx.config.linux_tracing.log_dir = ""

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={
                                "base_branch": "develop",
                                "pipeline_health": "true",
                                "is_fleet_dispatch": "false",
                                "dispatch_id": "",
                            },
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            await open_kitchen(name="demo", ctx=mock_ctx)

    call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
    overrides = call_kwargs["ingredient_overrides"]
    assert overrides["pipeline_health"] == "true", (
        f"Config default must apply; got overrides={overrides}"
    )

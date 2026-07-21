"""Tests for the _is_deferred_recall=True path: active_recipe_steps and fail-closed guard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_deferred_recall_sets_active_recipe_steps_from_recipe(tmp_path):
    """Deferred-recall path populates active_recipe_steps from the freshly loaded recipe."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    # Provide a real temp_dir so artifact_dir.mkdir succeeds under the mock.
    mock_ctx.temp_dir = tmp_path
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
async def test_deferred_recall_sets_active_recipe_steps_none_when_find_raises(tmp_path):
    """When recipes.find raises, active_recipe_steps is set to None and the call still succeeds."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    # Provide a real temp_dir so artifact_dir.mkdir succeeds under the mock.
    mock_ctx.temp_dir = tmp_path
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
async def test_deferred_recall_sets_active_recipe_steps_none_when_find_returns_none(tmp_path):
    """When recipes.find returns None (recipe not on disk), active_recipe_steps is None."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    # Provide a real temp_dir so artifact_dir.mkdir succeeds under the mock.
    mock_ctx.temp_dir = tmp_path
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
async def test_pre_reveal_then_open_does_not_re_execute_handler(tmp_path):
    """Pre-revealed state (gate enabled, recipe_name empty, infrastructure ready)
    must skip _open_kitchen_handler and still load the recipe."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_pre_revealed_ctx("test-recipe")
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    # Provide a real temp_dir so artifact_dir.mkdir succeeds under the mock.
    mock_ctx.temp_dir = tmp_path
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
            tools_kitchen, "_open_kitchen_handler", new_callable=AsyncMock
        ) as mock_handler,
    ):
        result = await tools_kitchen.open_kitchen(name="test-recipe", ctx=mock_ctx)

    mock_handler.assert_not_called()
    parsed = json.loads(result)
    assert parsed["success"] is True


@pytest.mark.anyio
async def test_deferred_recall_returns_infeasible_when_dispatch_feasible_false():
    """When deferred-recall loads a recipe with dispatch_feasible=False,
    open_kitchen must return the infeasible response, not proceed."""
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    mock_ctx = _make_deferred_recall_ctx("test-recipe")
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: test-recipe\nsteps:\n  gate:\n    cmd: echo\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
        "dispatch_feasible": False,
        "infeasible_steps": ["gate_backend_write"],
        "post_prune_step_names": ["gate_backend_write"],
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    mock_ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"gate_backend_write": MagicMock()}
    mock_ctx.recipes.load.return_value = mock_recipe_obj
    mock_ctx.disable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        result = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert "dispatch_infeasible" in str(parsed).lower() or "gate_backend_write" in str(parsed)


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
            tools_kitchen, "_open_kitchen_handler", new_callable=AsyncMock
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
async def test_cold_open_kitchen_runs_handler():
    """When gate_infrastructure_ready is False (cold state), handler must run."""
    from autoskillit.server.tools import tools_kitchen

    mock_ctx = _make_mock_ctx()
    mock_ctx.gate.enabled = False
    mock_ctx.gate_infrastructure_ready = False

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch.object(
            tools_kitchen, "_open_kitchen_handler", new_callable=AsyncMock
        ) as mock_handler,
    ):
        mock_handler.return_value = None
        result = await tools_kitchen.open_kitchen(name="test-recipe", ctx=mock_ctx)

    mock_handler.assert_called_once()
    assert isinstance(result, str)


@pytest.mark.anyio
async def test_deferred_recall_preserves_active_locks(tmp_path):
    """Locks survive across deferred-recall re-open (overlay file persists)."""
    from autoskillit.server.tools.tools_kitchen import lock_ingredients, open_kitchen

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text("{}")

    ctx = _make_deferred_recall_ctx("test-recipe")
    ctx.project_dir = tmp_path
    # New envelope: open_kitchen persists the full payload to temp_dir/responses/.
    # Provide a real temp_dir so artifact_dir.mkdir succeeds under the mock.
    ctx.temp_dir = tmp_path
    ctx.active_recipe_steps = {"investigate": MagicMock(skip_when_false="inputs.investigate")}
    ctx.active_recipe_ingredients = frozenset(["investigate"])
    ctx.gate.enabled = True
    ctx.recipes.load_and_validate.return_value = {
        "content": "name: test-recipe\nsteps:\n  investigate:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "--- TABLE ---",
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "post_prune_step_names": ["investigate"],
    }

    mock_recipe_info = MagicMock()
    mock_recipe_info.path = Path("/fake/.autoskillit/recipes/test-recipe.yaml")
    ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = {"investigate": MagicMock()}
    mock_recipe_obj.ingredients = {"investigate": MagicMock()}
    ctx.recipes.load.return_value = mock_recipe_obj

    with patch("autoskillit.server._get_ctx", return_value=ctx):
        # Lock investigate=false
        lock_result = json.loads(
            await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")
        )
        assert lock_result["success"] is True

        # Re-open kitchen (deferred-recall path) — locks must still be in effect
        result_str = await open_kitchen(name="test-recipe", ctx=ctx)
        parsed = json.loads(result_str)
        assert parsed["success"] is True

        overlay_path = temp_dir / ".hook_config_overlay.json"
        assert overlay_path.exists(), "Overlay must persist across re-open"
        data = json.loads(overlay_path.read_text())
        assert data["locked_ingredients"]["a"]["investigate"] == "false"

"""Tests for lock_ingredients MCP tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_step_mock(skip_when_false: str | None = None) -> MagicMock:
    step = MagicMock()
    step.skip_when_false = skip_when_false
    return step


class TestLockIngredientsBasic:
    """Test 1: lock_ingredients writes overlay."""

    @pytest.mark.anyio
    async def test_lock_ingredients_writes_overlay(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
            "build": _make_step_mock(None),
            "fix-worktree": _make_step_mock("inputs.investigate"),
        }

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")
            )

        assert result["success"] is True
        assert "locked" in result
        assert "locked_steps" in result

        overlay = temp_dir / ".hook_config_overlay.json"
        assert overlay.exists()
        data = json.loads(overlay.read_text())

        assert data["locked_ingredients"]["a"]["investigate"] == "false"
        assert data["locked_steps"]["a"]["investigate"] is False
        assert data["locked_steps"]["a"]["fix-worktree"] is False

    @pytest.mark.anyio
    async def test_lock_ingredients_writes_overlay_investigate_true(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {"investigate": _make_step_mock("inputs.investigate")}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "true"}, pipeline_id="a")
            )

        assert result["success"] is True
        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert data["locked_steps"]["a"]["investigate"] is True


class TestLockIngredientsRejectsServerAuthoritative:
    """Test 2: lock_ingredients rejects server-authoritative ingredients."""

    @pytest.mark.anyio
    async def test_lock_ingredients_rejects_server_authoritative(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"base_branch": "develop"}, pipeline_id="a")
            )

        assert result["success"] is False
        assert "server-authoritative" in result["error"].lower()


class TestLockIngredientsUnlock:
    """Test 3: lock_ingredients unlock."""

    @pytest.mark.anyio
    async def test_lock_ingredients_unlock(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {"investigate": _make_step_mock("inputs.investigate")}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            # Lock
            await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")

            # Unlock
            result = json.loads(await lock_ingredients(unlock=["investigate"], pipeline_id="a"))

        assert result["success"] is True
        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())

        assert "investigate" not in data.get("locked_ingredients", {}).get("a", {})
        assert "investigate" not in data.get("locked_steps", {}).get("a", {})


class TestLockIngredientsRequiresKitchenOpen:
    """Test 4: lock_ingredients requires kitchen open."""

    @pytest.mark.anyio
    async def test_lock_ingredients_requires_kitchen_open(self, tmp_path, monkeypatch):
        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(await lock_ingredients(locked={"investigate": "false"}))

        assert result["success"] is False
        assert "hook config file absent" in result["error"]


class TestLockIngredientsHeadlessDenial:
    """Test 5: lock_ingredients denies headless sessions."""

    @pytest.mark.anyio
    async def test_lock_ingredients_headless_denial(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            with monkeypatch.context() as m:
                m.setenv("AUTOSKILLIT_HEADLESS", "1")
                m.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
                from autoskillit.server.tools.tools_kitchen import lock_ingredients

                result = json.loads(await lock_ingredients(locked={"investigate": "false"}))

        assert result["success"] is False
        assert "skill" in result["result"].lower()


class TestUnlockRebuildsLockedSteps:
    """Test 14: unlock rebuilds locked_steps by step name, not ingredient name."""

    @pytest.mark.anyio
    async def test_unlock_rebuilds_locked_steps_by_step_name(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "fix-worktree": _make_step_mock("inputs.investigate"),
        }

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")
            )

        assert result["success"] is True
        assert result["locked_steps"]["fix-worktree"] is False

        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert "fix-worktree" in data["locked_steps"]["a"]

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result2 = json.loads(await lock_ingredients(unlock=["investigate"], pipeline_id="a"))

        assert result2["success"] is True
        assert result2["locked_steps"] == {}


class TestLockIngredientsConcurrentFlock:
    """Test 16: concurrent flock."""

    @pytest.mark.anyio
    async def test_lock_ingredients_concurrent_flock(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        results: list[dict] = []
        errors: list[Exception] = []

        async def call_lock(pipeline_id: str) -> None:
            try:
                ctx = _make_mock_ctx()
                ctx.project_dir = tmp_path
                ctx.active_recipe_steps = {f"step-{pipeline_id}": _make_step_mock(None)}
                with patch("autoskillit.server._get_ctx", return_value=ctx):
                    from autoskillit.server.tools.tools_kitchen import lock_ingredients

                    result = await lock_ingredients(
                        locked={"test": "true"}, pipeline_id=pipeline_id
                    )
                    results.append(json.loads(result))
            except Exception as e:
                errors.append(e)

        import asyncio

        await asyncio.gather(
            call_lock("a"),
            call_lock("b"),
        )

        assert len(errors) == 0
        assert all(r["success"] for r in results)

        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert "a" in data["locked_ingredients"]
        assert "b" in data["locked_ingredients"]

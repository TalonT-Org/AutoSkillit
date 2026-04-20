"""Tests for dispatch_food_truck tool handler and execute_dispatch domain function."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_standard_recipe(name: str = "test-recipe", ingredient_keys: list[str] | None = None):
    """Return a minimal Recipe with kind=STANDARD."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

    ingredients = {k: RecipeIngredient(description=k) for k in (ingredient_keys or [])}
    return Recipe(name=name, description="test", ingredients=ingredients, kind=RecipeKind.STANDARD)


def _make_campaign_recipe(name: str = "campaign-recipe"):
    """Return a minimal Recipe with kind=CAMPAIGN."""
    from autoskillit.recipe.schema import Recipe, RecipeKind

    return Recipe(name=name, description="test", kind=RecipeKind.CAMPAIGN)


def _simple_prompt_builder(**kwargs) -> str:
    """Minimal prompt builder for tests — avoids CLI imports."""
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


def _patch_quota(monkeypatch) -> None:
    """Patch check_and_sleep_if_needed to return no-sleep result."""
    monkeypatch.setattr(
        "autoskillit.execution.check_and_sleep_if_needed",
        AsyncMock(
            return_value={"should_sleep": False, "sleep_seconds": 0, "utilization": None,
                          "resets_at": None, "window_name": None}
        ),
    )


def _patch_quota_refresh(monkeypatch) -> None:
    """Patch _refresh_quota_cache so it returns a no-op coroutine."""
    async def _noop(_config):
        pass

    monkeypatch.setattr("autoskillit.execution._refresh_quota_cache", _noop)


# ---------------------------------------------------------------------------
# Class TestDispatchFoodTruckGates — headless refusal, kitchen gate, lock contention
# ---------------------------------------------------------------------------


class TestDispatchFoodTruckGates:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_hard_refusal_headless(self, tool_ctx, monkeypatch):
        """AUTOSKILLIT_HEADLESS=1 → franchise_hard_refusal_headless, regardless of SESSION_TYPE."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        from autoskillit.server.tools_execution import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["error"] == "franchise_hard_refusal_headless"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_requires_kitchen_open(self, tool_ctx, monkeypatch):
        """Kitchen closed → gate_error_result JSON."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.tools_execution import dispatch_food_truck

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_parallel_refused_when_locked(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """franchise_lock.locked() == True → franchise_parallel_refused error."""
        from autoskillit.franchise._api import execute_dispatch

        lock = asyncio.Lock()
        await lock.acquire()  # lock it
        tool_ctx.franchise_lock = lock
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        result = json.loads(
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="r",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        )
        assert result["success"] is False
        assert result["error"] == "franchise_parallel_refused"


# ---------------------------------------------------------------------------
# Class TestDispatchFoodTruckValidation — recipe kind, ingredient keys, non-string values
# ---------------------------------------------------------------------------


class TestDispatchFoodTruckValidation:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_non_standard_recipe(
        self, tool_ctx, monkeypatch
    ):
        """Campaign recipe → franchise_invalid_recipe_kind error."""
        from autoskillit.franchise._api import execute_dispatch

        tool_ctx.franchise_lock = asyncio.Lock()
        repo = InMemoryRecipeRepository()
        repo.add_recipe("campaign-recipe", _make_campaign_recipe("campaign-recipe"))
        tool_ctx.recipes = repo
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        result = json.loads(
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="campaign-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        )
        assert result["success"] is False
        assert result["error"] == "franchise_invalid_recipe_kind"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_unknown_ingredients(
        self, tool_ctx, monkeypatch
    ):
        """Keys not in recipe.ingredients → franchise_invalid_ingredients error."""
        from autoskillit.franchise._api import execute_dispatch

        tool_ctx.franchise_lock = asyncio.Lock()
        repo = InMemoryRecipeRepository()
        repo.add_recipe("test-recipe", _make_standard_recipe("test-recipe", ["task"]))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        result = json.loads(
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={"task": "v", "unknown_key": "bad"},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        )
        assert result["success"] is False
        assert result["error"] == "franchise_invalid_ingredients"
        assert "unknown_key" in result["user_visible_message"]

    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_non_string_values(
        self, tool_ctx, monkeypatch
    ):
        """Non-string ingredient values rejected before lock acquisition."""
        from autoskillit.franchise._api import execute_dispatch

        lock = asyncio.Lock()
        tool_ctx.franchise_lock = lock
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        result = json.loads(
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="r",
                task="t",
                ingredients={"key": 123},  # type: ignore[dict-item]
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        )
        assert result["success"] is False
        assert result["error"] == "franchise_invalid_ingredients"
        # Lock must not have been acquired
        assert not lock.locked()


# ---------------------------------------------------------------------------
# Class TestDispatchFoodTruckExecution — lock lifecycle, success, pid, quota, cleanup
# ---------------------------------------------------------------------------


class TestDispatchFoodTruckExecution:
    def _setup_standard_dispatch(self, tool_ctx):
        """Wire tool_ctx for a successful standard dispatch."""
        tool_ctx.franchise_lock = asyncio.Lock()
        repo = InMemoryRecipeRepository()
        repo.add_recipe("test-recipe", _make_standard_recipe("test-recipe", ["task"]))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_success(
        self, tool_ctx, monkeypatch
    ):
        """Lock released after successful dispatch."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
        )
        assert not tool_ctx.franchise_lock.locked()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_exception(
        self, tool_ctx, monkeypatch
    ):
        """Lock released when executor raises."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        tool_ctx.executor.dispatch_food_truck = AsyncMock(
            side_effect=RuntimeError("executor crashed")
        )
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        result = json.loads(
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        )
        assert result["success"] is False
        assert not tool_ctx.franchise_lock.locked()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_cancellation(
        self, tool_ctx, monkeypatch
    ):
        """Lock released on asyncio.CancelledError."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        tool_ctx.executor.dispatch_food_truck = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
            )
        assert not tool_ctx.franchise_lock.locked()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_success_envelope(self, tool_ctx, monkeypatch):
        """Returns {success, dispatch_id, l2_session_id, l2_payload, token_usage}."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        from tests.fakes import _DEFAULT_SKILL_RESULT
        import dataclasses

        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                result="dispatch done",
                session_id="sess-abc",
                token_usage={"input_tokens": 100},
            )
        )
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        raw = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="complete the task",
            ingredients={"task": "override-task"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
        )
        result = json.loads(raw)
        assert result["success"] is True
        assert "dispatch_id" in result
        assert result["l2_session_id"] == "sess-abc"
        assert result["l2_payload"] == "dispatch done"
        assert result["token_usage"] == {"input_tokens": 100}

    @pytest.mark.anyio
    async def test_dispatch_food_truck_on_spawn_writes_pid(self, tool_ctx, monkeypatch):
        """on_spawn callback writes l2_pid into state.json via mark_dispatch_running."""
        from autoskillit.franchise._api import _write_pid
        from autoskillit.franchise.state import DispatchRecord, write_initial_state

        state_path = tool_ctx.temp_dir / "dispatches" / "test-dispatch.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        write_initial_state(
            state_path,
            campaign_id="kitchen-id",
            campaign_name="test-dispatch-name",
            manifest_path="",
            dispatches=[DispatchRecord(name="test-dispatch-name")],
        )

        _write_pid(state_path, "test-dispatch-name", "dispatch-id-abc", 54321)

        state_data = json.loads(state_path.read_text())
        dispatch_record = state_data["dispatches"][0]
        assert dispatch_record["l2_pid"] == 54321
        assert dispatch_record["status"] == "running"
        assert dispatch_record["dispatch_id"] == "dispatch-id-abc"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_passes_on_spawn_to_executor(
        self, tool_ctx, monkeypatch
    ):
        """execute_dispatch passes a callable on_spawn to the executor."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
        )
        on_spawn = tool_ctx.executor.dispatch_calls[0].on_spawn
        assert callable(on_spawn)

    @pytest.mark.anyio
    async def test_dispatch_food_truck_invalidates_quota_cache(
        self, tool_ctx, monkeypatch
    ):
        """After dispatch completes, quota cache is refreshed via background supervisor."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        _patch_quota(monkeypatch)

        submitted_labels: list[str] = []

        def _capture_submit(coro, label: str = "") -> None:
            submitted_labels.append(label)
            coro.close()

        monkeypatch.setattr(tool_ctx.background, "submit", _capture_submit)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
        )
        assert "quota_post_dispatch_refresh" in submitted_labels

    @pytest.mark.anyio
    async def test_dispatch_food_truck_cleans_session_skills(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """Completed L2 session skill dir is cleaned up."""
        from autoskillit.franchise._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx)
        import dataclasses
        from tests.fakes import _DEFAULT_SKILL_RESULT

        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                session_id="l2-session-xyz",
            )
        )
        _patch_quota(monkeypatch)
        _patch_quota_refresh(monkeypatch)

        cleanup_calls: list[str] = []

        def _capture_cleanup(session_id: str) -> bool:
            cleanup_calls.append(session_id)
            return False

        monkeypatch.setattr(
            tool_ctx.session_skill_manager, "cleanup_session", _capture_cleanup
        )

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
        )
        assert "l2-session-xyz" in cleanup_calls

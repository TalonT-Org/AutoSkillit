"""Tests for dispatch_food_truck execution: lock, success, PID, quota, cleanup."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.fleet import FleetSemaphore
from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
from tests.server._helpers import (
    _make_recipe_info,
    _make_standard_recipe,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _simple_prompt_builder,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.feature("fleet")]


class TestDispatchFoodTruckExecution:
    def _setup_standard_dispatch(self, tool_ctx, monkeypatch):
        """Wire tool_ctx for a successful standard dispatch."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe", ["task"]))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_success(self, tool_ctx, monkeypatch):
        """Lock released after successful dispatch."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        assert not tool_ctx.fleet_lock.at_capacity()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_exception(self, tool_ctx, monkeypatch):
        """Lock released when executor raises."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor.dispatch_food_truck = AsyncMock(
            side_effect=RuntimeError("executor crashed")
        )

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert not tool_ctx.fleet_lock.at_capacity()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_releases_lock_on_cancellation(self, tool_ctx, monkeypatch):
        """Lock released on asyncio.CancelledError."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor.dispatch_food_truck = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )
        assert not tool_ctx.fleet_lock.at_capacity()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_success_envelope(self, tool_ctx, monkeypatch):
        """Returns envelope with success, dispatch_id, l3_payload, token_usage, l3_parse_source."""
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.fleet.result_parser import L3ParseResult
        from tests.fakes import _DEFAULT_SKILL_RESULT

        self._setup_standard_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                result="dispatch done",
                session_id="sess-abc",
                token_usage={"input_tokens": 100},
            )
        )

        canned_payload = {"success": True, "data": "dispatch done"}
        canned_result = L3ParseResult(
            outcome="completed_clean",
            payload=canned_payload,
            raw_body=None,
            parse_error=None,
            source="stdout",
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_kwargs: canned_result,
        )

        raw = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="complete the task",
            ingredients={"task": "override-task"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(raw.outcome.to_envelope())
        assert result["success"] is True
        assert "dispatch_id" in result
        assert result["dispatched_session_id"] == "sess-abc"
        assert result["l3_payload"] == canned_payload
        from autoskillit.fleet.state import normalize_dispatch_token_usage

        assert result["token_usage"] == normalize_dispatch_token_usage({"input_tokens": 100})
        assert result["l3_parse_source"] == "stdout"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_on_spawn_writes_pid(self, tool_ctx, monkeypatch):
        """on_spawn callback writes dispatched_pid into state.json via mark_dispatch_running."""
        from autoskillit.fleet._api import _write_pid
        from autoskillit.fleet.state import DispatchRecord, write_initial_state

        state_path = tool_ctx.temp_dir / "dispatches" / "test-dispatch.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        write_initial_state(
            state_path,
            campaign_id="kitchen-id",
            campaign_name="test-dispatch-name",
            manifest_path="",
            dispatches=[DispatchRecord(name="test-dispatch-name")],
        )

        _write_pid(state_path, "test-dispatch-name", "dispatch-id-abc", 54321, 0, boot_id="")

        state_data = json.loads(state_path.read_text())
        dispatch_record = state_data["dispatches"][0]
        assert dispatch_record["dispatched_pid"] == 54321
        assert dispatch_record["status"] == "running"
        assert dispatch_record["dispatch_id"] == "dispatch-id-abc"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_passes_on_spawn_to_executor(self, tool_ctx, monkeypatch):
        """execute_dispatch passes an on_spawn that writes the PID to the state file."""
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.fleet.state import read_state

        self._setup_standard_dispatch(tool_ctx, monkeypatch)

        # Wrap dispatch_food_truck to invoke on_spawn before returning,
        # simulating the real headless executor calling the callback on process start.
        original_dispatch = tool_ctx.executor.dispatch_food_truck

        async def _dispatch_invoking_spawn(*args, on_spawn=None, **kwargs):
            result = await original_dispatch(*args, on_spawn=on_spawn, **kwargs)
            if on_spawn is not None:
                on_spawn(99999, 0)
            return result

        monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _dispatch_invoking_spawn)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        dispatch_id = tool_ctx.executor.dispatch_calls[0].order_id
        state_path = tool_ctx.temp_dir / "dispatches" / f"{dispatch_id}.json"
        state = read_state(state_path)
        assert state is not None
        assert any(d.dispatched_pid == 99999 for d in state.dispatches)

    @pytest.mark.anyio
    async def test_dispatch_food_truck_invalidates_quota_cache(self, tool_ctx, monkeypatch):
        """After dispatch completes, quota cache is refreshed via background supervisor."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)

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
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        assert "quota_post_dispatch_refresh" in submitted_labels

    @pytest.mark.anyio
    async def test_dispatch_food_truck_does_not_call_cleanup_session(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """Fleet dispatch does not call cleanup_session (no init_session on this path)."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)

        cleanup_calls: list[str] = []

        def _capture_cleanup(session_id: str) -> bool:
            cleanup_calls.append(session_id)
            return False

        monkeypatch.setattr(tool_ctx.session_skill_manager, "cleanup_session", _capture_cleanup)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        assert len(cleanup_calls) == 0

    @pytest.mark.anyio
    async def test_dispatch_food_truck_invalidates_quota_cache_file(self, tool_ctx, monkeypatch):
        """After dispatch, cache_invalidator is called with the configured cache path."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_standard_dispatch(tool_ctx, monkeypatch)

        invalidate_calls: list[str] = []

        def _capture_invalidate(cache_path: str) -> None:
            invalidate_calls.append(cache_path)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            cache_invalidator=_capture_invalidate,
        )

        assert tool_ctx.config.quota_guard.cache_path in invalidate_calls

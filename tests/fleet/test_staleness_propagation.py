"""Tests for staleness error propagation through fleet dispatch."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import FleetErrorCode
from autoskillit.fleet import FleetSemaphore
from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
from tests.server._helpers import _make_recipe_info, _make_standard_recipe

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    pass


def _simple_prompt_builder(**kwargs) -> str:
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


class TestStalenessErrorPropagation:
    @pytest.mark.anyio
    async def test_stale_process_does_not_block_dispatch(self, tool_ctx, monkeypatch):
        """Fleet dispatch must proceed despite process staleness — L2 revalidates."""
        from autoskillit.fleet._api import execute_dispatch

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe", ["task"]))
        repo.set_stale(True)
        tool_ctx.recipes = repo
        executor = InMemoryHeadlessExecutor()
        tool_ctx.executor = executor

        dispatch_result = await execute_dispatch(
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
        result = json.loads(dispatch_result.outcome.to_envelope())
        # Must NOT be FLEET_PROCESS_STALE — dispatch should proceed
        assert result.get("error") != FleetErrorCode.FLEET_PROCESS_STALE
        # Executor must have been called (dispatch proceeded past validation)
        assert len(executor.dispatch_calls) == 1

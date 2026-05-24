"""Tests for dispatch_food_truck parameter passthrough: resume and idle_timeout."""

from __future__ import annotations

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


@pytest.mark.anyio
async def test_dispatch_food_truck_tool_passes_resume_session_id_to_executor(
    tool_ctx_kitchen_open, monkeypatch
):
    """dispatch_food_truck MCP tool forwards resume_session_id all the way to the executor."""
    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    tool_ctx_kitchen_open.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
    tool_ctx_kitchen_open.recipes = repo
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    await dispatch_food_truck(
        recipe="test-recipe",
        task="do-work",
        resume_session_id="sess-resume-123",
    )

    assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
    assert executor.dispatch_calls[0].resume_session_id == "sess-resume-123"


@pytest.mark.anyio
async def test_dispatch_food_truck_tool_passes_resume_message_to_executor(
    tool_ctx_kitchen_open, monkeypatch
):
    """dispatch_food_truck MCP tool forwards resume_message all the way to the executor."""
    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    tool_ctx_kitchen_open.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
    tool_ctx_kitchen_open.recipes = repo
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    await dispatch_food_truck(
        recipe="test-recipe",
        task="do-work",
        resume_session_id="sess-resume-123",
        resume_message="retry with new quota",
    )

    assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
    assert executor.dispatch_calls[0].resume_message == "retry with new quota"


@pytest.mark.anyio
async def test_dispatch_food_truck_tool_passes_caller_instructions_into_prompt(
    tool_ctx_kitchen_open, monkeypatch
):
    """dispatch_food_truck MCP tool embeds caller_instructions in the orchestrator prompt."""
    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    tool_ctx_kitchen_open.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
    tool_ctx_kitchen_open.recipes = repo
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    await dispatch_food_truck(
        recipe="test-recipe",
        task="do-work",
        caller_instructions="use opus for implement",
    )

    assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
    assert "CALLER INSTRUCTIONS" in executor.dispatch_calls[0].orchestrator_prompt
    assert "use opus for implement" in executor.dispatch_calls[0].orchestrator_prompt


@pytest.mark.anyio
async def test_dispatch_food_truck_no_caller_instructions_section_when_not_specified(
    tool_ctx_kitchen_open, monkeypatch
):
    """When caller_instructions is not specified, no CALLER INSTRUCTIONS section appears in prompt."""
    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    tool_ctx_kitchen_open.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
    tool_ctx_kitchen_open.recipes = repo
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    await dispatch_food_truck(
        recipe="test-recipe",
        task="do-work",
    )

    assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
    assert "CALLER INSTRUCTIONS" not in executor.dispatch_calls[0].orchestrator_prompt


class TestDispatchFoodTruckIdleTimeout:
    """Tests for idle_output_timeout passthrough through the dispatch chain."""

    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    def _setup_dispatch(self, tool_ctx):
        """Wire tool_ctx for a standard dispatch with InMemoryHeadlessExecutor."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_passes_idle_output_timeout_to_executor(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """dispatch_food_truck MCP tool forwards idle_output_timeout to executor."""
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        self._setup_dispatch(tool_ctx_kitchen_open)

        await dispatch_food_truck(
            recipe="test-recipe",
            task="do-work",
            idle_output_timeout=0,
        )

        executor = tool_ctx_kitchen_open.executor
        assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
        assert executor.dispatch_calls[0].idle_output_timeout == 0.0

    @pytest.mark.anyio
    async def test_dispatch_food_truck_idle_timeout_none_when_not_specified(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """When idle_output_timeout is not specified, executor receives None."""
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        self._setup_dispatch(tool_ctx_kitchen_open)

        await dispatch_food_truck(
            recipe="test-recipe",
            task="do-work",
        )

        executor = tool_ctx_kitchen_open.executor
        assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
        assert executor.dispatch_calls[0].idle_output_timeout is None

    @pytest.mark.anyio
    async def test_dispatch_food_truck_idle_timeout_overrides_config_default(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """Explicit idle_output_timeout=0 overrides the config default of 1000."""
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        self._setup_dispatch(tool_ctx_kitchen_open)
        # Config idle_output_timeout is 1000 (default from RunSkillConfig)
        assert tool_ctx_kitchen_open.config.run_skill.idle_output_timeout == 1000

        await dispatch_food_truck(
            recipe="test-recipe",
            task="do-work",
            idle_output_timeout=0,
        )

        executor = tool_ctx_kitchen_open.executor
        assert executor.dispatch_calls, "dispatch_food_truck executor was never called"
        # Executor receives 0.0, overriding the config default
        assert executor.dispatch_calls[0].idle_output_timeout == 0.0

    @pytest.mark.anyio
    async def test_execute_dispatch_passes_idle_output_timeout_to_executor(
        self, tool_ctx, monkeypatch
    ):
        """execute_dispatch forwards idle_output_timeout to executor.dispatch_food_truck."""
        from autoskillit.fleet._api import execute_dispatch

        self._setup_dispatch(tool_ctx)

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do-work",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            idle_output_timeout=0,
        )

        executor = tool_ctx.executor
        assert executor.dispatch_calls, "dispatch_food_truck was never called"
        assert executor.dispatch_calls[0].idle_output_timeout == 0.0

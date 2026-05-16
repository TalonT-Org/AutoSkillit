"""Tests for dispatch_food_truck campaign halt enforcement gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet import FleetSemaphore
from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
from tests.server._helpers import _make_recipe_info, _make_standard_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _write_campaign_state(state_path: Path, dispatches: list[dict]) -> None:
    """Write a minimal campaign state file with the given dispatch records."""
    import time

    from autoskillit.fleet import FLEET_STATE_SCHEMA_VERSION

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": FLEET_STATE_SCHEMA_VERSION,
                "campaign_id": "test-campaign",
                "campaign_name": "test",
                "manifest_path": "/fake/manifest.yaml",
                "started_at": time.time(),
                "dispatches": dispatches,
                "captured_values": {},
            }
        )
    )


class TestDispatchFoodTruckHaltEnforcement:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_dispatch_refuses_after_failure_when_halt_enabled(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """Prior FAILURE + continue_on_failure=false → FLEET_CAMPAIGN_HALTED."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [{"name": "d1", "status": "failure"}])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["error"] == "fleet_campaign_halted"

    @pytest.mark.anyio
    async def test_dispatch_proceeds_after_failure_when_continue_enabled(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """dispatch_food_truck proceeds when continue_on_failure=true even with prior failure."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [{"name": "d1", "status": "failure"}])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "true")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_dispatch_proceeds_when_no_campaign_state_path(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """Without AUTOSKILLIT_CAMPAIGN_STATE_PATH, halt check is skipped."""
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", raising=False)

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_dispatch_proceeds_when_no_failures_in_state(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """dispatch_food_truck proceeds when all prior dispatches are SUCCESS."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [{"name": "d1", "status": "success"}])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_dispatch_proceeds_when_state_file_missing(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """If state file doesn't exist, halt check is skipped (fail-open)."""
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(tmp_path / "nonexistent.json"))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_campaign_state_write_uses_envelope_dispatch_status(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """Campaign-state write reads dispatch_status from envelope, not success flag.

        Mocks execute_dispatch to return an envelope with dispatch_status='resumable'
        and success=False. Verifies the campaign-state write uses RESUMABLE, not FAILURE.
        """
        from unittest.mock import AsyncMock

        from autoskillit.fleet import DispatchStatus, read_state, write_initial_state

        state_path = tmp_path / "campaign_state.json"
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        write_initial_state(state_path, "cid", "camp", "/m.yaml", [])

        from autoskillit.fleet import DispatchCompleted, DispatchResult
        from autoskillit.fleet import DispatchStatus as _DS

        monkeypatch.setattr(
            "autoskillit.fleet.execute_dispatch",
            AsyncMock(
                return_value=DispatchResult(
                    outcome=DispatchCompleted(
                        success=False,
                        dispatch_status=_DS.RESUMABLE,
                        dispatch_id="test-dispatch-id",
                        dispatched_session_id="sess-abc",
                        reason="fleet_l3_no_result_block",
                        token_usage={},
                        l3_parse_source="stdout",
                        lifespan_started=True,
                        l3_payload=None,
                    ),
                    per_dispatch_state_path=None,
                )
            ),
        )

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        await dispatch_food_truck(recipe="test-recipe", task="t", dispatch_name="d1")

        state = read_state(state_path)
        assert state is not None
        written = next(
            (d for d in state.dispatches if d.name == "d1" and d.status != DispatchStatus.PENDING),
            None,
        )
        assert written is not None, "No campaign dispatch record was written for d1"
        assert written.status == DispatchStatus.RESUMABLE, (
            f"Expected RESUMABLE but got {written.status}"
        )

    @pytest.mark.anyio
    async def test_no_result_block_failure_does_not_halt_campaign(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """dispatch_food_truck proceeds when prior FAILURE has fleet_l3_no_result_block reason."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(
            state_path,
            [{"name": "d1", "status": "failure", "reason": "fleet_l3_no_result_block"}],
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert "dispatch_id" in result

    def _setup_standard_dispatch(self, tool_ctx, monkeypatch):
        """Wire tool_ctx for a successful standard dispatch."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()


class TestPostDispatchHaltOnFailure:
    """Post-dispatch halt: FAILURE result + continue_on_failure=false → FLEET_CAMPAIGN_HALTED."""

    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_dispatch_food_truck_halts_after_failure_when_continue_on_failure_false(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """After a dispatch returns FAILURE with continue_on_failure=false,
        the next dispatch_food_truck call must return FLEET_CAMPAIGN_HALTED immediately,
        before calling execute_dispatch.
        """
        from unittest.mock import AsyncMock

        state_path = tmp_path / "campaign_state.json"
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        # Write a FAILURE dispatch record into campaign state
        _write_campaign_state(state_path, [{"name": "run-design", "status": "failure"}])

        mock_execute = AsyncMock()
        monkeypatch.setattr(
            "autoskillit.fleet.execute_dispatch",
            mock_execute,
        )

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))

        # Must return FLEET_CAMPAIGN_HALTED immediately — execute_dispatch must NOT be called
        assert result["success"] is False
        assert result["error"] == "fleet_campaign_halted"
        mock_execute.assert_not_called()

    def _setup_standard_dispatch(self, tool_ctx, monkeypatch):
        """Wire tool_ctx for a standard dispatch."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()


class TestDispatchFoodTruckRetryOnFailure:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_dispatch_resets_and_proceeds_when_retrying_failed_dispatch(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """dispatch_name matches failed dispatch → reset to PENDING, proceed."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(
            state_path, [{"name": "d1", "status": "failure", "reason": "task_failed"}]
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(
            await dispatch_food_truck(recipe="test-recipe", task="t", dispatch_name="d1")
        )
        assert "dispatch_id" in result, "Expected dispatch to run, not be blocked by fleet halt"

    @pytest.mark.anyio
    async def test_dispatch_halts_when_different_dispatch_has_failure(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """dispatch_name does NOT match failed dispatch → still halts."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(
            state_path, [{"name": "d1", "status": "failure", "reason": "task_failed"}]
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(
            await dispatch_food_truck(recipe="test-recipe", task="t", dispatch_name="d2")
        )
        assert result["success"] is False
        assert result["error"] == "fleet_campaign_halted"

    @pytest.mark.anyio
    async def test_dispatch_halts_when_no_dispatch_name_provided(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """No dispatch_name + prior failure → halts (current behavior)."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(
            state_path, [{"name": "d1", "status": "failure", "reason": "task_failed"}]
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="test-recipe", task="t"))
        assert result["success"] is False
        assert result["error"] == "fleet_campaign_halted"

    def _setup_standard_dispatch(self, tool_ctx, monkeypatch):
        """Wire tool_ctx for a successful standard dispatch."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()


class TestDispatchFoodTruckSkipWhen:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    def _write_dispatch_capture(
        self, dispatches_dir: Path, campaign_id: str, captured: dict
    ) -> None:
        """Write a dispatch capture JSON file with captured values for skip_when resolution."""
        import time

        from autoskillit.fleet import FLEET_STATE_SCHEMA_VERSION

        dispatches_dir.mkdir(parents=True, exist_ok=True)
        state_file = dispatches_dir / f"capture_{campaign_id}.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": FLEET_STATE_SCHEMA_VERSION,
                    "campaign_id": campaign_id,
                    "campaign_name": "test",
                    "manifest_path": "/fake/manifest.yaml",
                    "started_at": time.time(),
                    "dispatches": [{"name": "prior-dispatch", "status": "success"}],
                    "captured_values": captured,
                }
            )
        )

    @pytest.mark.anyio
    async def test_skip_when_true_returns_skipped(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """skip_when=true returns FLEET_DISPATCH_SKIPPED without calling execute_dispatch."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(
            await dispatch_food_truck(
                recipe="test-recipe", task="t", skip_when="'local' == 'local'"
            )
        )
        assert result["success"] is False
        assert result["error"] == "fleet_dispatch_skipped"

    @pytest.mark.anyio
    async def test_skip_when_false_proceeds_normally(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """skip_when evaluating to false proceeds with execute_dispatch normally."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(
            await dispatch_food_truck(recipe="test-recipe", task="t", skip_when="'pr' == 'local'")
        )
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_skip_when_resolves_campaign_ref(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """skip_when resolves ${{ campaign.X }} from accumulated captures."""
        state_path = tmp_path / "state.json"
        _write_campaign_state(state_path, [])
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(state_path))
        monkeypatch.setenv("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")

        dispatches_dir = tool_ctx_kitchen_open.temp_dir / "dispatches"
        self._write_dispatch_capture(
            dispatches_dir, tool_ctx_kitchen_open.kitchen_id, {"output_mode": "local"}
        )
        self._setup_standard_dispatch(tool_ctx_kitchen_open, monkeypatch)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(
            await dispatch_food_truck(
                recipe="test-recipe",
                task="t",
                skip_when="${{ campaign.output_mode }} == 'local'",
            )
        )
        assert result["success"] is False
        assert result["error"] == "fleet_dispatch_skipped"

    def _setup_standard_dispatch(self, tool_ctx, monkeypatch):
        """Wire tool_ctx for a successful standard dispatch."""
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

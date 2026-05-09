"""Tests for gate dispatch state persistence and campaign state writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet import (
    DispatchCompleted,
    DispatchRecord,
    DispatchStatus,
    read_state,
    resume_campaign_from_state,
    write_initial_state,
)
from tests.fleet.conftest import fleet_lock_from_ctx

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _init_state(tmp_path: Path, *names: str) -> Path:
    sp = _state_path(tmp_path)
    write_initial_state(
        sp, "cid", "test-campaign", "/m.yaml", [DispatchRecord(name=n) for n in names]
    )
    return sp


# ---------------------------------------------------------------------------
# Tests 1-5: record_gate_dispatch MCP tool
# ---------------------------------------------------------------------------


class TestRecordGateDispatch:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_record_gate_dispatch_writes_success(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "gate-check", "phase-one")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=True)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["status"] == "success"

        state = read_state(sp)
        assert state is not None
        gate = next(d for d in state.dispatches if d.name == "gate-check")
        assert gate.status == DispatchStatus.SUCCESS
        phase = next(d for d in state.dispatches if d.name == "phase-one")
        assert phase.status == DispatchStatus.PENDING

    @pytest.mark.anyio
    async def test_record_gate_dispatch_writes_refused(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "gate-check", "phase-one")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=False)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["status"] == "refused"

        state = read_state(sp)
        assert state is not None
        gate = next(d for d in state.dispatches if d.name == "gate-check")
        assert gate.status == DispatchStatus.REFUSED

    @pytest.mark.anyio
    async def test_record_gate_dispatch_rejects_unknown_dispatch(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit", "review-gate")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="nonexistent", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_unknown_dispatch"

    @pytest.mark.anyio
    async def test_record_gate_dispatch_rejects_non_pending(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.fleet.state import append_dispatch_record

        sp = _init_state(tmp_path, "gate-check", "phase-one")
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="gate-check", status=DispatchStatus.SUCCESS, reason="gate_approved"
            ),
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_already_recorded"

    @pytest.mark.anyio
    async def test_record_gate_dispatch_requires_campaign_state_path(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", raising=False)

        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_no_campaign"


# ---------------------------------------------------------------------------
# Tests 6-9: dispatch_food_truck campaign state persistence
# ---------------------------------------------------------------------------


class TestDispatchFoodTruckCampaignState:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_dispatch_food_truck_updates_campaign_state_on_success(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return DispatchCompleted(
                success=True,
                dispatch_status=DispatchStatus.SUCCESS,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="",
                token_usage={},
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name="full-audit")

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.SUCCESS

    @pytest.mark.anyio
    async def test_dispatch_food_truck_updates_campaign_state_on_failure(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return DispatchCompleted(
                success=False,
                dispatch_status=DispatchStatus.FAILURE,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="l2_crashed",
                token_usage={},
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name="full-audit")

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.FAILURE

    @pytest.mark.anyio
    async def test_dispatch_food_truck_skips_campaign_state_without_env(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return DispatchCompleted(
                success=True,
                dispatch_status=DispatchStatus.SUCCESS,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="",
                token_usage={},
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        raw = await dispatch_food_truck(
            recipe="full-audit", task="audit", dispatch_name="full-audit"
        )
        result = json.loads(raw)
        assert result["success"] is True
        # No state file should exist
        assert not (tmp_path / "campaign" / "state.json").exists()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_uses_recipe_name_when_dispatch_name_is_none(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """Verify dispatch_name=None falls back to recipe name for campaign state write."""
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return DispatchCompleted(
                success=True,
                dispatch_status=DispatchStatus.SUCCESS,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="",
                token_usage={},
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name=None)

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.SUCCESS


# ---------------------------------------------------------------------------
# Test 11: resume chain for promote-to-main shape
# ---------------------------------------------------------------------------


class TestCampaignResumeChain:
    def test_campaign_dispatch_chain_resume_after_two_successes(self, tmp_path):
        from autoskillit.fleet.state import append_dispatch_record

        sp = _init_state(
            tmp_path, "full-audit", "review-gate", "build-map", "implement-findings", "promote"
        )
        append_dispatch_record(
            sp,
            DispatchRecord(name="full-audit", status=DispatchStatus.SUCCESS, reason="completed"),
        )
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="review-gate", status=DispatchStatus.SUCCESS, reason="gate_approved"
            ),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)
        assert decision is not None
        assert decision.next_dispatch_name == "build-map"
        assert "full-audit" in decision.completed_dispatches_block
        assert "review-gate" in decision.completed_dispatches_block


def test_refused_gate_resume_selects_next(tmp_path):
    sp = _init_state(tmp_path, "gate-check", "phase-one")
    from autoskillit.fleet.state_gates import record_gate_outcome

    record_gate_outcome(sp, "gate-check", approved=False)
    state = read_state(sp)
    assert state is not None
    gate_dispatch = next(d for d in state.dispatches if d.name == "gate-check")
    assert gate_dispatch.status == DispatchStatus.REFUSED
    decision = resume_campaign_from_state(sp, continue_on_failure=True)
    assert decision is not None
    assert decision.next_dispatch_name == "phase-one"
    assert "- gate-check: refused" in decision.completed_dispatches_block


# ---------------------------------------------------------------------------
# Tests T1-T6: validation-failure state persistence
# ---------------------------------------------------------------------------


class TestValidationFailureCampaignState:
    @pytest.fixture(autouse=True)
    def _set_fleet_session(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @pytest.mark.anyio
    async def test_execute_dispatch_records_type_rejection_to_campaign_state(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """T1: execute_dispatch writes REFUSED to campaign state on type validation error."""

        sp = _init_state(tmp_path, "step1")
        fleet_lock_from_ctx(tool_ctx)

        from autoskillit.fleet import execute_dispatch

        # Trigger FLEET_UNKNOWN_INGREDIENT (non-string ingredient value)
        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"key": 123},  # type error — values must be strings
            dispatch_name="step1",
            timeout_sec=None,
            prompt_builder=lambda **kw: "",
            quota_checker=lambda **kw: {},
            quota_refresher=lambda **kw: None,
            campaign_state_path=sp,
        )
        state = read_state(sp)
        d = next(d for d in state.dispatches if d.name == "step1")
        assert d.status == DispatchStatus.REFUSED
        assert d.reason == "fleet_unknown_ingredient"

    @pytest.mark.anyio
    async def test_run_dispatch_writes_per_dispatch_state_on_missing_ingredient(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """T2: _run_dispatch creates per-dispatch state file on missing required ingredient."""
        from autoskillit.core import RecipeSource
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_path = tmp_path / "test-recipe.yaml"
        repo.add_recipe(
            "test-recipe",
            RecipeInfo(
                name="test-recipe",
                description="test",
                source=RecipeSource.PROJECT,
                path=recipe_path,
            ),
        )
        repo.add_full_recipe(
            recipe_path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={"required_key": RecipeIngredient(description="", required=True)},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo

        from autoskillit.fleet import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={},  # missing required_key
            dispatch_name="step1",
            timeout_sec=None,
            prompt_builder=lambda **kw: "",
            quota_checker=lambda **kw: {},
            quota_refresher=lambda **kw: None,
            campaign_state_path=None,
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatch_files = list(dispatches_dir.glob("*.json"))
        assert len(dispatch_files) == 1
        with open(dispatch_files[0]) as f:
            records = [DispatchRecord.from_dict(r) for r in json.load(f)["dispatches"]]
        refused = [r for r in records if r.status == DispatchStatus.REFUSED]
        assert len(refused) >= 1
        assert refused[0].reason == "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_run_dispatch_writes_campaign_state_on_unknown_ingredient(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """T3: _run_dispatch writes REFUSED to campaign state on unknown ingredient."""
        from autoskillit.core import RecipeSource
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        sp = _init_state(tmp_path, "step1")

        repo = InMemoryRecipeRepository()
        recipe_path = tmp_path / "test-recipe.yaml"
        repo.add_recipe(
            "test-recipe",
            RecipeInfo(
                name="test-recipe",
                description="test",
                source=RecipeSource.PROJECT,
                path=recipe_path,
            ),
        )
        repo.add_full_recipe(
            recipe_path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={"task": RecipeIngredient(description="", required=False)},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo

        from autoskillit.fleet import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"nonexistent_key": "val"},  # unknown ingredient
            dispatch_name="step1",
            timeout_sec=None,
            prompt_builder=lambda **kw: "",
            quota_checker=lambda **kw: {},
            quota_refresher=lambda **kw: None,
            campaign_state_path=sp,
        )
        state = read_state(sp)
        d = next(d for d in state.dispatches if d.name == "step1")
        assert d.status == DispatchStatus.REFUSED
        assert d.reason == "fleet_unknown_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_writes_campaign_state_without_dispatch_name(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        """T4: dispatch_food_truck uses recipe name when dispatch_name is None."""
        sp = _init_state(tmp_path, "my-recipe")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return DispatchCompleted(
                success=False,
                dispatch_status=DispatchStatus.FAILURE,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="fleet_missing_ingredient",
                token_usage={},
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        raw = await dispatch_food_truck(recipe="my-recipe", task="t", dispatch_name=None)
        result = json.loads(raw)
        assert result["success"] is False

        state = read_state(sp)
        d = next(d for d in state.dispatches if d.name == "my-recipe")
        assert d.status == DispatchStatus.FAILURE

    @pytest.mark.anyio
    async def test_campaign_resume_halts_on_validation_refused(self, tmp_path):
        """T5: resume_campaign_from_state halts on REFUSED dispatch."""
        from autoskillit.fleet.state import append_dispatch_record

        sp = _init_state(tmp_path, "step1")
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="step1",
                status=DispatchStatus.REFUSED,
                reason="fleet_missing_ingredient",
            ),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False, reset_on_retry=False)
        assert decision is not None
        assert decision.completed_dispatches_block == "fleet_halted_on_failure"

    @pytest.mark.anyio
    async def test_dispatch_rejected_carries_dispatch_id(self, tool_ctx, monkeypatch, tmp_path):
        """T6: DispatchRejected returned from execute_dispatch carries non-empty dispatch_id."""
        from autoskillit.core import RecipeSource
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_path = tmp_path / "test-recipe.yaml"
        repo.add_recipe(
            "test-recipe",
            RecipeInfo(
                name="test-recipe",
                description="test",
                source=RecipeSource.PROJECT,
                path=recipe_path,
            ),
        )
        repo.add_full_recipe(
            recipe_path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={"required_key": RecipeIngredient(description="", required=True)},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo

        from autoskillit.fleet import execute_dispatch

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={},
            dispatch_name="step1",
            timeout_sec=None,
            prompt_builder=lambda **kw: "",
            quota_checker=lambda **kw: {},
            quota_refresher=lambda **kw: None,
            campaign_state_path=None,
        )

        assert hasattr(result, "dispatch_id")
        assert result.dispatch_id != ""

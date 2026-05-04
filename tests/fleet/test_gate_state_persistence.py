"""Tests for gate dispatch state persistence and campaign state writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    resume_campaign_from_state,
    write_initial_state,
)

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
    @pytest.mark.anyio
    async def test_record_gate_dispatch_writes_success(self, tool_ctx, monkeypatch, tmp_path):
        sp = _init_state(tmp_path, "gate-check", "phase-one")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_execution import record_gate_dispatch

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
    async def test_record_gate_dispatch_writes_failure(self, tool_ctx, monkeypatch, tmp_path):
        sp = _init_state(tmp_path, "gate-check", "phase-one")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_execution import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=False)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["status"] == "failure"

        state = read_state(sp)
        assert state is not None
        gate = next(d for d in state.dispatches if d.name == "gate-check")
        assert gate.status == DispatchStatus.FAILURE

    @pytest.mark.anyio
    async def test_record_gate_dispatch_rejects_unknown_dispatch(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit", "review-gate")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_execution import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="nonexistent", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_unknown_dispatch"

    @pytest.mark.anyio
    async def test_record_gate_dispatch_rejects_non_pending(self, tool_ctx, monkeypatch, tmp_path):
        from autoskillit.fleet.state import append_dispatch_record

        sp = _init_state(tmp_path, "gate-check", "phase-one")
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="gate-check", status=DispatchStatus.SUCCESS, reason="gate_approved"
            ),
        )
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))

        from autoskillit.server.tools.tools_execution import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_already_recorded"

    @pytest.mark.anyio
    async def test_record_gate_dispatch_requires_campaign_state_path(self, tool_ctx, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", raising=False)

        from autoskillit.server.tools.tools_execution import record_gate_dispatch

        raw = await record_gate_dispatch(dispatch_name="gate-check", approved=True)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_gate_no_campaign"


# ---------------------------------------------------------------------------
# Tests 6-9: dispatch_food_truck campaign state persistence
# ---------------------------------------------------------------------------


class TestDispatchFoodTruckCampaignState:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_updates_campaign_state_on_success(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        success_envelope = json.dumps(
            {
                "success": True,
                "dispatch_status": "success",
                "dispatch_id": "d1",
                "l2_session_id": "s1",
                "reason": "",
                "token_usage": {},
            }
        )

        async def _fake_execute(**kwargs):
            return success_envelope

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_execution import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name="full-audit")

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.SUCCESS

    @pytest.mark.anyio
    async def test_dispatch_food_truck_updates_campaign_state_on_failure(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        failure_envelope = json.dumps(
            {
                "success": False,
                "dispatch_status": "failure",
                "dispatch_id": "d1",
                "l2_session_id": "s1",
                "reason": "l2_crashed",
                "token_usage": {},
            }
        )

        async def _fake_execute(**kwargs):
            return failure_envelope

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_execution import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name="full-audit")

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.FAILURE

    @pytest.mark.anyio
    async def test_dispatch_food_truck_skips_campaign_state_without_env(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return json.dumps(
                {
                    "success": True,
                    "dispatch_id": "d1",
                    "l2_session_id": "s1",
                    "reason": "",
                    "token_usage": {},
                }
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_execution import dispatch_food_truck

        raw = await dispatch_food_truck(
            recipe="full-audit", task="audit", dispatch_name="full-audit"
        )
        result = json.loads(raw)
        assert result["success"] is True
        # No state file should exist
        assert not (tmp_path / "campaign" / "state.json").exists()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_skips_campaign_state_without_dispatch_name(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        sp = _init_state(tmp_path, "full-audit")
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_STATE_PATH", str(sp))
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

        async def _fake_execute(**kwargs):
            return json.dumps(
                {
                    "success": True,
                    "dispatch_id": "d1",
                    "l2_session_id": "s1",
                    "reason": "",
                    "token_usage": {},
                }
            )

        import autoskillit.fleet

        monkeypatch.setattr(autoskillit.fleet, "execute_dispatch", _fake_execute)

        from autoskillit.server.tools.tools_execution import dispatch_food_truck

        await dispatch_food_truck(recipe="full-audit", task="audit", dispatch_name=None)

        state = read_state(sp)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "full-audit")
        assert d.status == DispatchStatus.PENDING


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

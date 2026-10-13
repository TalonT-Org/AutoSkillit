"""Tests for complete_run_skill_result and get_pipeline_report tracker-gap detection."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import complete_run_skill_result
from tests.server._pipeline_test_helpers import _publish_success_receipt

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestCompleteRunSkillResult:
    @pytest.mark.anyio
    async def test_unknown_step_receipt_tracker_parity(self, tool_ctx_kitchen_open, tmp_path):
        from types import SimpleNamespace

        from autoskillit.server.tools.tools_kitchen._open_kitchen._tracker_auto_init import (
            _auto_init_pipeline_tracker,
        )
        from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step
        from tests.server._pipeline_test_helpers import _setup_project

        _setup_project(tmp_path, tool_ctx_kitchen_open)
        assert _auto_init_pipeline_tracker(tool_ctx_kitchen_open) is None
        kitchen_target = tool_ctx_kitchen_open.kitchen_tracker_key.target
        kitchen_tracker = json.loads(kitchen_target.path.read_text())
        assert isinstance(kitchen_tracker["tracker_incarnation_id"], str)
        assert kitchen_tracker["tracker_incarnation_id"]

        manual_pipeline_id = "manual-receipt-tracker"
        initialized = json.loads(
            await record_pipeline_step(pipeline_id=manual_pipeline_id, op="init")
        )
        assert initialized["success"] is True
        manual_path = (
            tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / f"{manual_pipeline_id}.json"
        )
        manual_tracker = json.loads(manual_path.read_text())

        tracker_cases = (
            (
                kitchen_target.target_order_id,
                kitchen_target.path,
                kitchen_tracker,
            ),
            (manual_pipeline_id, manual_path, manual_tracker),
        )
        results = []
        for pipeline_id, tracker_path, tracker in tracker_cases:
            receipt = _publish_success_receipt(
                tool_ctx_kitchen_open,
                pipeline_id=pipeline_id,
                tracker_path=tracker_path,
                tracker_kitchen_id=tracker["kitchen_id"],
                tracker_incarnation_id=tracker["tracker_incarnation_id"],
                step_name="unknown-tracker-step",
            )
            before = tracker_path.read_bytes()
            result = json.loads(
                await complete_run_skill_result(
                    receipt.receipt_id,
                    ctx=SimpleNamespace(session_id="request-session"),
                )
            )
            after = tracker_path.read_bytes()

            assert before == after
            results.append(result)

        assert results[0]["tracker"] == results[1]["tracker"]
        assert results[0]["tracker"]["success"] is False
        assert results[0]["tracker"]["stage"] == "mark_step_complete"
        assert results[0]["tracker_repairable"] is results[1]["tracker_repairable"]
        assert results[0]["tracker_repairable"] is True

    @pytest.mark.anyio
    async def test_tracker_preparation_failure_keeps_acknowledged_credit_repairable(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace

        from autoskillit.server import _tracker_authority

        authority = tool_ctx_kitchen_open.run_skill_completion
        assert authority is not None
        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        receipt = _publish_success_receipt(
            tool_ctx_kitchen_open,
            pipeline_id="AB",
            tracker_path=tracker_path,
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
        )

        def fail_retain(*_args, **_kwargs):
            raise OSError("lease unavailable")

        monkeypatch.setattr(_tracker_authority, "_retain_context_tracker", fail_retain)
        result = json.loads(
            await complete_run_skill_result(
                receipt.receipt_id,
                ctx=SimpleNamespace(session_id="request-session"),
            )
        )

        assert result["success"] is True
        assert result["tracker_repairable"] is True
        repaired = authority.apply_tracker_credit(
            tracker_order_id="AB",
            tracker_path=str(tracker_path.resolve()),
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
            receipt_id=receipt.receipt_id,
            effect=lambda: {"success": True},
        )
        assert repaired["success"] is True

    @pytest.mark.anyio
    async def test_receipt_tracker_path_must_use_project_authority(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from types import SimpleNamespace

        authority = tool_ctx_kitchen_open.run_skill_completion
        assert authority is not None
        outside_tracker = tmp_path / "outside" / "AB.json"
        invocation_id = authority.begin(
            kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            request_session_id="request-session",
            tracker_order_id="AB",
            tracker_path=str(outside_tracker.resolve()),
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
        )
        receipt = authority.draft(
            invocation_id,
            classification="success",
            success=True,
            result_digest="digest",
        )
        authority.publish(receipt.receipt_id)

        result = json.loads(
            await complete_run_skill_result(
                receipt.receipt_id,
                ctx=SimpleNamespace(session_id="request-session"),
            )
        )

        assert result["success"] is True
        assert result["tracker"]["stage"] == "tracker_credit"
        assert result["tracker_repairable"] is True
        assert not outside_tracker.with_suffix(".lease.lock").exists()


class TestGetPipelineReportIncludesTrackerGaps:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        tool_ctx_kitchen_open.project_dir = tmp_path
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_get_pipeline_report_includes_tracker_gaps(self):
        from autoskillit.server.tools.tools_status import get_pipeline_report

        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete"},
                        "implement": {"status": "pending"},
                    },
                    "dependencies": {},
                }
            )
        )

        result = json.loads(await get_pipeline_report())
        assert "step_completion_gaps" in result
        gaps = result["step_completion_gaps"]
        assert any(g["pipeline_id"] == "AB" and g["step"] == "implement" for g in gaps)

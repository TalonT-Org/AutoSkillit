"""Tests for complete_run_skill_result and get_pipeline_report tracker-gap detection."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import complete_run_skill_result
from tests.server._pipeline_test_helpers import _publish_success_receipt

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestCompleteRunSkillResult:
    @pytest.mark.anyio
    async def test_tracker_preparation_failure_keeps_acknowledged_credit_repairable(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace

        from autoskillit.server.tools import tools_pipeline_tracker

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

        monkeypatch.setattr(tools_pipeline_tracker, "_retain_context_tracker", fail_retain)
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

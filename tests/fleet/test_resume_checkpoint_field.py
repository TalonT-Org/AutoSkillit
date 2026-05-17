"""Tests for resume_checkpoint field on DispatchRecord and checkpoint on ResumeDecision."""

from __future__ import annotations

import pytest

from autoskillit.fleet.state_types import (
    DispatchRecord,
    DispatchStatus,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestDispatchRecordResumeCheckpoint:
    def test_dispatch_record_has_resume_checkpoint_field(self):
        """DispatchRecord must have a resume_checkpoint field."""
        record = DispatchRecord(name="step-1", status=DispatchStatus.PENDING)
        # The field should exist and default to empty dict
        assert hasattr(record, "resume_checkpoint")
        assert record.resume_checkpoint == {}

    def test_resume_checkpoint_accepts_nested_dict(self):
        """resume_checkpoint must accept a nested dict with completed_items."""
        record = DispatchRecord(
            name="step-1",
            status=DispatchStatus.RESUMABLE,
            resume_checkpoint={
                "completed_items": ["issue-1", "issue-2"],
                "step_name": "process-issues",
            },
        )
        assert record.resume_checkpoint["completed_items"] == ["issue-1", "issue-2"]
        assert record.resume_checkpoint["step_name"] == "process-issues"


class TestResumeDecisionHasCheckpoint:
    def test_resume_decision_has_resume_checkpoint_field(self):
        """ResumeDecision must have a resume_checkpoint field."""
        from autoskillit.fleet.state_types import ResumeDecision

        decision = ResumeDecision(
            next_dispatch_name="step-2",
            completed_dispatches_block="",
            is_resumable=True,
        )
        assert hasattr(decision, "resume_checkpoint")
        assert decision.resume_checkpoint == {}

    def test_resume_decision_resume_checkpoint_accepts_dict(self):
        """ResumeDecision.resume_checkpoint must accept a dict with completed_items."""
        from autoskillit.fleet.state_types import ResumeDecision

        decision = ResumeDecision(
            next_dispatch_name="step-2",
            completed_dispatches_block="",
            is_resumable=True,
            resume_checkpoint={"completed_items": ["issue-1"]},
        )
        assert decision.resume_checkpoint["completed_items"] == ["issue-1"]

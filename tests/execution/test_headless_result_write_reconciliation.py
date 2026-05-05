"""Integration tests for EMPTY_OUTPUT + write-evidence reconciliation gate.

Verifies that _build_skill_result() reclassifies EMPTY_OUTPUT to COMPLETED_NO_FLUSH
when write evidence exists, preserving partial worktree progress instead of discarding it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from autoskillit.core.types import (
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless import _build_skill_result
from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_WRITE_TOOL_LINE = json.dumps(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "id": "w1",
                    "input": {"file_path": "/worktree/src/foo.py"},
                }
            ]
        },
    }
)
_EMPTY_OUTPUT_RESULT_LINE = json.dumps(
    {
        "type": "result",
        "subtype": "empty_output",
        "is_error": True,
        "result": "",
        "session_id": "",
    }
)
_SUCCESS_EMPTY_RESULT_LINE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "session_id": "s1",
    }
)


def _make_sr(stdout: str) -> SubprocessResult:
    return SubprocessResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


class TestEmptyOutputWriteReconciliation:
    """EMPTY_OUTPUT + write evidence must be reclassified to COMPLETED_NO_FLUSH."""

    def test_empty_output_with_write_tool_use_becomes_completed_no_flush(self) -> None:
        """Write tool_use in stdout triggers reclassification from EMPTY_OUTPUT."""
        stdout = "\n".join([_WRITE_TOOL_LINE, _EMPTY_OUTPUT_RESULT_LINE])
        sr = _build_skill_result(_make_sr(stdout))

        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.COMPLETED_NO_FLUSH
        assert sr.retry_reason != RetryReason.EMPTY_OUTPUT
        assert sr.write_call_count > 0

    def test_empty_output_with_fs_writes_only_becomes_completed_no_flush(self) -> None:
        """fs_writes_detected alone (no tool_uses) triggers reclassification."""
        stdout = _EMPTY_OUTPUT_RESULT_LINE
        sr = _build_skill_result(_make_sr(stdout), fs_writes_detected=True)

        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.COMPLETED_NO_FLUSH
        assert sr.retry_reason != RetryReason.EMPTY_OUTPUT

    def test_success_empty_result_with_write_evidence_becomes_completed_no_flush(self) -> None:
        """success subtype with empty result + write evidence → COMPLETED_NO_FLUSH."""
        stdout = "\n".join([_WRITE_TOOL_LINE, _SUCCESS_EMPTY_RESULT_LINE])
        sr = _build_skill_result(_make_sr(stdout))

        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.COMPLETED_NO_FLUSH
        assert sr.retry_reason != RetryReason.EMPTY_OUTPUT

    def test_empty_output_without_write_evidence_stays_empty_output(self) -> None:
        """EMPTY_OUTPUT with no writes preserves original reason — correct behavior unchanged."""
        stdout = _EMPTY_OUTPUT_RESULT_LINE
        sr = _build_skill_result(_make_sr(stdout), fs_writes_detected=False)

        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.EMPTY_OUTPUT

    def test_budget_guard_caps_completed_no_flush(self) -> None:
        """Budget exhaustion overrides COMPLETED_NO_FLUSH → BUDGET_EXHAUSTED."""
        stdout = "\n".join([_WRITE_TOOL_LINE, _EMPTY_OUTPUT_RESULT_LINE])
        skill_command = "/autoskillit:implement-worktree-no-merge"
        audit = DefaultAuditLog()
        for _ in range(4):  # 4 > max_consecutive_retries=3
            audit.record_failure(
                FailureRecord(  # type: ignore[arg-type]
                    timestamp=datetime.now(UTC).isoformat(),
                    skill_command=skill_command,
                    exit_code=-1,
                    subtype="empty_output",
                    needs_retry=True,
                    retry_reason="empty_output",
                    stderr="",
                )
            )

        sr = _build_skill_result(
            _make_sr(stdout),
            skill_command=skill_command,
            audit=audit,
        )

        assert sr.retry_reason == RetryReason.BUDGET_EXHAUSTED
        assert sr.needs_retry is False

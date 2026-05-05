"""Write-evidence invariants: 'no work done' retry reasons must be overridden by write evidence.

Architectural immunity test: any RetryReason asserting 'no work done' must be reclassified
when write evidence contradicts the assertion. Adding a new 'no work' reason without a
corresponding gate causes this test to fail immediately.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless import _build_skill_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# Reasons that carry a "no work done" semantic assertion.
# Any reason in this set must be reclassified when write evidence is present.
NO_WORK_REASONS = {RetryReason.EMPTY_OUTPUT}

# Reasons that correctly reflect write-aware reclassification.
WRITE_AWARE_REASONS = {RetryReason.COMPLETED_NO_FLUSH}

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


@pytest.mark.parametrize("reason", sorted(NO_WORK_REASONS, key=lambda r: r.value))
def test_no_work_reasons_are_overridden_by_write_evidence(reason: RetryReason) -> None:
    """Any 'no work done' retry reason must be reclassified when write evidence exists."""
    stdout = "\n".join([_WRITE_TOOL_LINE, _EMPTY_OUTPUT_RESULT_LINE])
    result = SubprocessResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )
    sr = _build_skill_result(result, fs_writes_detected=True)

    assert sr.retry_reason != reason, (
        f"RetryReason.{reason.name} was not overridden despite write evidence. "
        f"Add a write-evidence reconciliation gate for this reason."
    )
    assert sr.retry_reason in WRITE_AWARE_REASONS, (
        f"Expected reclassification to one of {WRITE_AWARE_REASONS!r}, got {sr.retry_reason!r}"
    )

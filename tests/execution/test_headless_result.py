"""Tests for _build_skill_result idle_stall lifespan_started propagation."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.headless import _build_skill_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small, pytest.mark.feature("fleet")]


def _idle_stall_result(stdout: str) -> SubprocessResult:
    """Build a SubprocessResult with IDLE_STALL termination."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.IDLE_STALL,
        pid=12345,
        session_id="sess-idle-1",
        channel_b_session_id="",
    )


def _tool_use_ndjson(tool_name: str = "Write", **input_kwargs: object) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": tool_name,
                        "id": "tool-1",
                        "input": input_kwargs,
                    }
                ]
            },
        }
    )


class TestIdleStallLifespanStarted:
    def test_idle_stall_failure_preserves_lifespan_started_true(self):
        stdout = _tool_use_ndjson("Write", file_path="/worktree/src/foo.py")
        result = _idle_stall_result(stdout)
        skill_result = _build_skill_result(result)
        assert skill_result.lifespan_started is True

    def test_idle_stall_failure_preserves_lifespan_started_false(self):
        result = _idle_stall_result("")
        skill_result = _build_skill_result(result)
        assert skill_result.lifespan_started is False

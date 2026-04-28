"""Shared fixtures and helpers for tests/execution/."""

from __future__ import annotations

import json
import pathlib
import textwrap
from typing import Any

import pytest

from autoskillit.core.types import SubprocessResult, TerminationReason
from tests._helpers import make_tracing_config


def _success_session_json(result_text: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": result_text,
            "session_id": "test-session",
            "is_error": False,
        }
    )


def _sr(
    returncode=0,
    stdout="",
    stderr="",
    termination=TerminationReason.NATURAL_EXIT,
    session_id: str = "",
    channel_b_session_id: str = "",
):
    """Build a minimal SubprocessResult for _build_skill_result tests."""
    return SubprocessResult(
        returncode,
        stdout,
        stderr,
        termination,
        pid=12345,
        session_id=session_id,
        channel_b_session_id=channel_b_session_id,
    )


def _make_tool_use_line(name: str, input_dict: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": name, "id": "x", "input": input_dict}]
            },
        }
    )


# Simulates Claude CLI process that writes a result line then hangs.
# Used by test_process_channel_b.py and test_process_monitor.py.
WRITE_RESULT_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Shared helper script used by PTY-tracing and session-log integration tests.
# Allocates 60 MB resident memory then sleeps so the tracer can observe it.
_ALLOCATE_60MB_SCRIPT = """\
import time
data = bytearray(60 * 1024 * 1024)  # 60 MB resident allocation
time.sleep(3)
"""


@pytest.fixture
def isolated_tracing_config(tmp_path: pathlib.Path):
    """Pre-isolated LinuxTracingConfig for tracing tests.
    Always writes to a tmp_path subdir, never to the real /dev/shm.
    Use this fixture for all new tests that need a LinuxTracingConfig."""
    shm = tmp_path / "shm"
    shm.mkdir(parents=True, exist_ok=True)
    return make_tracing_config(enabled=True, proc_interval=0.05, tmpfs_path=str(shm))


@pytest.fixture
def merge_group_only_repo_state() -> dict[str, Any]:
    """A fixture modelling a repo whose CI only triggers on merge_group.

    Forces the merge-group-only scenario to exist as a first-class test input.
    Used by reachability tests and test_check_repo_merge_state_returns_merge_group_as_ci_event.
    """
    return {
        "graphql_response": {
            "data": {
                "repository": {
                    "mergeQueue": None,
                    "autoMergeAllowed": True,
                    "object": {
                        "entries": [
                            {
                                "name": "tests.yml",
                                "object": {"text": "on: [merge_group]\njobs: {}"},
                            },
                        ]
                    },
                }
            }
        },
        "rest_completed_runs": {"workflow_runs": []},
        "rest_active_runs": {"workflow_runs": []},
    }

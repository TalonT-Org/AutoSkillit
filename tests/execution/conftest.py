"""Shared fixtures and helpers for tests/execution/."""

from __future__ import annotations

import json
import pathlib
import textwrap
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher, PRFetchState
from autoskillit.execution.session import ClaudeSessionResult
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


WRITE_TOOL_LINE = json.dumps(
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
EMPTY_OUTPUT_RESULT_LINE = json.dumps(
    {
        "type": "result",
        "subtype": "empty_output",
        "is_error": True,
        "result": "",
        "session_id": "",
    }
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


def _make_cc_jsonl_record(
    *,
    request_id: str = "",
    timestamp: str = "",
    content: list[dict] | None = None,
    record_type: str = "assistant",
) -> str:
    rec: dict[str, object] = {"type": record_type}
    if request_id:
        rec["requestId"] = request_id
    if timestamp:
        rec["timestamp"] = timestamp
    if content is not None:
        rec["message"] = {"content": content}
    return json.dumps(rec)


def _make_thinking_block(text: str = "reasoning...") -> dict[str, str]:
    return {"type": "thinking", "thinking": text}


def _make_tool_block(name: str) -> dict[str, object]:
    return {"type": "tool_use", "name": name, "id": "x", "input": {}}


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


def _snap(
    *,
    captured_at: str = "2026-03-03T12:00:00+00:00",
    vm_rss_kb: int = 100000,
    oom_score: int = 50,
    fd_count: int = 10,
    fd_soft_limit: int = 1024,
    state: str = "sleeping",
) -> dict[str, object]:
    return {
        "captured_at": captured_at,
        "state": state,
        "vm_rss_kb": vm_rss_kb,
        "oom_score": oom_score,
        "fd_count": fd_count,
        "fd_soft_limit": fd_soft_limit,
        "sig_pnd": "0000000000000000",
        "sig_blk": "0000000000000000",
        "sig_cgt": "0000000000000000",
        "threads": 4,
        "wchan": "",
        "ctx_switches_voluntary": 500,
        "ctx_switches_involuntary": 20,
    }


def _flush(tmp_path: Path, **overrides) -> None:
    from autoskillit.core.types._type_results import SessionTelemetry
    from autoskillit.execution.session_log import flush_session_log

    defaults: dict = {
        "log_dir": str(tmp_path),
        "cwd": "/home/test/project",
        "session_id": "test-session-001",
        "pid": 12345,
        "skill_command": "/autoskillit:investigate some error",
        "success": True,
        "subtype": "completed",
        "exit_code": 0,
        "start_ts": "2026-03-03T12:00:00+00:00",
        "proc_snapshots": [_snap(), _snap(), _snap()],
        "dispatch_id": "",
        "github_api_log": None,
        "token_usage": None,
        "timing_seconds": None,
        "audit_record": None,
        "loc_insertions": 0,
        "loc_deletions": 0,
    }
    defaults.update(overrides)

    # Extract telemetry kwargs and build SessionTelemetry before forwarding
    _github_api_log = defaults.pop("github_api_log")
    _session_id = defaults.get("session_id", "")
    _api_usage = _github_api_log.drain(_session_id) if _github_api_log is not None else None
    telemetry = SessionTelemetry(
        token_usage=defaults.pop("token_usage"),
        timing_seconds=defaults.pop("timing_seconds"),
        audit_record=defaults.pop("audit_record"),
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=defaults.pop("loc_insertions"),
        loc_deletions=defaults.pop("loc_deletions"),
    )
    flush_session_log(**defaults, telemetry=telemetry)


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


def _make_watcher() -> DefaultMergeQueueWatcher:
    return DefaultMergeQueueWatcher(token=None)


def _queue_state(
    *,
    merged: bool = False,
    state: str = "OPEN",
    mergeable: str = "MERGEABLE",
    merge_state_status: str = "CLEAN",
    auto_merge_present: bool = False,
    auto_merge_enabled_at: datetime | None = None,
    pr_node_id: str = "PR_kwDO_test",
    in_queue: bool = False,
    queue_state: str | None = None,
    checks_state: str | None = None,
    merge_group_checks_state: str | None = None,
) -> PRFetchState:
    return {
        "merged": merged,
        "state": state,
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "auto_merge_present": auto_merge_present,
        "auto_merge_enabled_at": auto_merge_enabled_at,
        "pr_node_id": pr_node_id,
        "in_queue": in_queue,
        "queue_state": queue_state,
        "checks_state": checks_state,
        "merge_group_checks_state": merge_group_checks_state,
    }


def _make_success_session(result: str = "done") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="s1",
    )


@pytest.fixture
def make_session() -> Callable[..., ClaudeSessionResult]:
    def _factory(
        subtype: str = "success",
        is_error: bool = False,
        result: str = "",
        assistant_messages: list[str] | None = None,
    ) -> ClaudeSessionResult:
        return ClaudeSessionResult(
            subtype=subtype,
            is_error=is_error,
            result=result,
            session_id="test-session",
            assistant_messages=assistant_messages or [],
        )

    return _factory

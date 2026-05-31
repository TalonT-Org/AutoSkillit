"""Tests for flush_session_log field coverage: write warnings, call counts,
kitchen/order IDs, crash exception, raw stdout, per-turn fields, tool calls,
silent gap, proc-trace exit snapshot, versions block, and recipe identity."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from autoskillit.core.types._type_results import ProviderOutcome
from autoskillit.core.types._type_results_execution import (
    RecipeIdentity,
    SessionTelemetry,
)
from autoskillit.execution.session_log import (
    flush_session_log,
)
from tests.execution.conftest import (
    _flush,
    _make_cc_jsonl_record,
    _make_thinking_block,
    _make_tool_block,
    _snap,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_flush_session_log_includes_write_path_warnings_in_summary(tmp_path):
    """summary.json records write_path_warnings list."""
    warnings = [
        "Write tool wrote to /source/repo/.autoskillit/temp/foo.md (outside cwd /clone)",
        "Edit tool wrote to /source/repo/src/file.py (outside cwd /clone)",
    ]
    _flush(tmp_path, session_id="warn-session", write_path_warnings=warnings, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "warn-session" / "summary.json").read_text())
    assert summary["write_path_warnings"] == warnings


def test_flush_session_log_empty_warnings_produce_empty_list(tmp_path):
    """No warnings → write_path_warnings is [] in summary."""
    _flush(tmp_path, session_id="clean-session", write_path_warnings=[], proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "clean-session" / "summary.json").read_text())
    assert summary["write_path_warnings"] == []


def test_flush_session_log_none_warnings_treated_as_empty(tmp_path):
    """write_path_warnings=None (default) produces empty list in summary."""
    _flush(tmp_path, session_id="default-warn", proc_snapshots=None)  # no write_path_warnings arg
    summary = json.loads((tmp_path / "sessions" / "default-warn" / "summary.json").read_text())
    assert summary["write_path_warnings"] == []


def test_flush_session_log_includes_write_call_count_in_summary(tmp_path):
    """summary.json records write_call_count."""
    _flush(tmp_path, session_id="wc-session", write_call_count=5, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "wc-session" / "summary.json").read_text())
    assert summary["write_call_count"] == 5


def test_flush_session_log_write_call_count_in_index(tmp_path):
    """sessions.jsonl index includes write_call_count."""
    _flush(tmp_path, session_id="wc-idx", write_call_count=3, proc_snapshots=None)
    index_path = tmp_path / "sessions.jsonl"
    entry = json.loads(index_path.read_text().strip().split("\n")[-1])
    assert entry["write_call_count"] == 3


def test_flush_session_log_write_call_count_defaults_to_zero(tmp_path):
    """write_call_count defaults to 0 when not specified."""
    _flush(tmp_path, session_id="wc-default", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "wc-default" / "summary.json").read_text())
    assert summary["write_call_count"] == 0


def test_flush_session_log_writes_kitchen_id(tmp_path):
    """kitchen_id parameter is written to sessions.jsonl index entry."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="my-pipeline-123",
        session_id="sess-001",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )

    index = (tmp_path / "sessions.jsonl").read_text()
    entry = json.loads(index.strip())
    assert entry["kitchen_id"] == "my-pipeline-123"


def test_flush_session_log_writes_order_id_to_index(tmp_path):
    """order_id is written to sessions.jsonl index entry when provided."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="kitchen-abc",
        order_id="issue-185",
        session_id="sess-002",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )

    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["order_id"] == "issue-185"


def test_flush_session_log_order_id_defaults_to_empty(tmp_path):
    """order_id defaults to empty string when not supplied."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="kitchen-abc",
        session_id="sess-003",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )

    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert "order_id" in entry
    assert entry["order_id"] == ""


def test_flush_writes_crash_exception_file(tmp_path):
    """When exception_text is provided, flush_session_log writes crash_exception.txt."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="test-session",
        pid=1234,
        skill_command="/test",
        success=False,
        subtype="crashed",
        exit_code=-1,
        start_ts=datetime.now(UTC).isoformat(),
        proc_snapshots=None,
        termination_reason="CRASHED",
        exception_text="RuntimeError: boom\n  at headless.py:1023",
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    session_dir = tmp_path / "sessions" / "test-session"
    crash_file = session_dir / "crash_exception.txt"
    assert crash_file.exists()
    assert "RuntimeError: boom" in crash_file.read_text()


# ---------------------------------------------------------------------------
# raw_stdout and per-turn field tests
# ---------------------------------------------------------------------------


def test_flush_session_log_writes_raw_stdout_on_failure(tmp_path):
    raw = '{"type": "assistant"}\n{"type": "result"}\n'
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="test-session",
        pid=1,
        skill_command="test",
        success=False,
        subtype="empty_output",
        exit_code=-1,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        raw_stdout=raw,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    raw_file = tmp_path / "sessions" / "test-session" / "raw_stdout.jsonl"
    assert raw_file.exists()
    assert raw_file.read_text() == raw


def test_flush_session_log_no_raw_stdout_on_success(tmp_path):
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="ok-session",
        pid=1,
        skill_command="test",
        success=True,
        subtype="success",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        raw_stdout='{"type": "result"}',
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    raw_file = tmp_path / "sessions" / "ok-session" / "raw_stdout.jsonl"
    assert not raw_file.exists()


def test_flush_session_log_summary_contains_per_turn_fields(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        json.dumps(
            {"type": "assistant", "requestId": "req-001", "timestamp": "2026-04-15T07:00:00Z"}
        )
        + "\n"
        + json.dumps(
            {"type": "assistant", "requestId": "req-002", "timestamp": "2026-04-15T07:00:05Z"}
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=False,
        subtype="empty_output",
        exit_code=-1,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        last_stop_reason="end_turn",
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["last_stop_reason"] == "end_turn"
    assert summary["request_ids"] == ["req-001", "req-002"]
    assert summary["turn_timestamps"] == ["2026-04-15T07:00:00Z", "2026-04-15T07:00:05Z"]


def test_flush_session_log_includes_no_request_id_turns(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            request_id="req-001",
            timestamp="2026-05-01T10:00:00Z",
            content=[_make_tool_block("Read")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:05Z",
            content=[_make_tool_block("Edit")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-01T10:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert len(summary["turn_timestamps"]) == 2
    assert len(summary["turn_tool_calls"]) == 2
    assert len(summary["request_ids"]) == 2
    assert summary["request_ids"][0] == "req-001"
    assert summary["request_ids"][1].startswith("turn-")
    assert (
        len(summary["turn_timestamps"])
        == len(summary["turn_tool_calls"])
        == len(summary["request_ids"])
    )


def test_flush_session_log_all_no_rid_turns_still_recorded(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:00Z",
            content=[_make_tool_block("Bash")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:05Z",
            content=[_make_tool_block("Read")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:10Z",
            content=[_make_tool_block("Edit")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-01T10:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert len(summary["turn_timestamps"]) == 3
    assert len(summary["turn_tool_calls"]) == 3
    assert len(summary["request_ids"]) == 3
    assert summary["turn_timestamps"] == [
        "2026-05-01T10:00:00Z",
        "2026-05-01T10:00:05Z",
        "2026-05-01T10:00:10Z",
    ]
    assert all(rid.startswith("turn-") for rid in summary["request_ids"])


def test_channel_b_turn_count_bounded_by_channel_a(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:00Z",
            content=[_make_tool_block("Bash")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:05Z",
            content=[_make_tool_block("Read")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    telemetry = SessionTelemetry(
        token_usage={"input_tokens": 0, "output_tokens": 0, "turn_count": 2},
        timing_seconds=None,
        audit_record=None,
        github_api_usage=None,
        github_api_requests=0,
        loc_insertions=0,
        loc_deletions=0,
    )
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-01T10:00:00Z",
        proc_snapshots=None,
        telemetry=telemetry,
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    tu = json.loads((tmp_path / "sessions" / "s" / "token_usage.json").read_text())
    turn_count = tu.get("turn_count", 0)
    assert len(summary["turn_timestamps"]) == 2
    assert turn_count >= len(summary["turn_timestamps"])


def test_parallel_lists_aligned_mixed_rid_no_rid(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            request_id="req-a",
            timestamp="2026-05-01T10:00:00Z",
            content=[_make_tool_block("ToolA")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:01Z",
            content=[_make_tool_block("ToolB")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            request_id="req-b",
            timestamp="2026-05-01T10:00:02Z",
            content=[_make_tool_block("ToolC")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            timestamp="2026-05-01T10:00:03Z",
            content=[_make_tool_block("ToolD")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            request_id="req-c",
            timestamp="2026-05-01T10:00:04Z",
            content=[_make_tool_block("ToolE")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-01T10:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert (
        len(summary["request_ids"])
        == len(summary["turn_timestamps"])
        == len(summary["turn_tool_calls"])
        == 5
    )
    assert summary["request_ids"] == ["req-a", "turn-0", "req-b", "turn-1", "req-c"]
    assert summary["turn_tool_calls"] == [
        ["ToolA"],
        ["ToolB"],
        ["ToolC"],
        ["ToolD"],
        ["ToolE"],
    ]
    assert summary["turn_timestamps"] == [
        "2026-05-01T10:00:00Z",
        "2026-05-01T10:00:01Z",
        "2026-05-01T10:00:02Z",
        "2026-05-01T10:00:03Z",
        "2026-05-01T10:00:04Z",
    ]


# turn_tool_calls


def test_flush_session_log_summary_contains_turn_tool_calls(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        json.dumps(
            {
                "type": "assistant",
                "requestId": "req-001",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "ToolA"},
                        {"type": "tool_use", "name": "ToolB"},
                    ]
                },
            }
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["turn_tool_calls"] == [["ToolA", "ToolB"]]


def test_turn_tool_calls_capped_at_8_per_turn(tmp_path, monkeypatch):
    tools = [{"type": "tool_use", "name": f"Tool{i}"} for i in range(10)]
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        json.dumps(
            {
                "type": "assistant",
                "requestId": "req-001",
                "message": {"content": tools},
            }
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert len(summary["turn_tool_calls"][0]) == 8
    assert summary["turn_tool_calls"][0] == [f"Tool{i}" for i in range(8)]


def test_turn_tool_calls_empty_for_text_only_turn(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        json.dumps(
            {
                "type": "assistant",
                "requestId": "req-001",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["turn_tool_calls"] == [[]]


def test_turn_tool_calls_parallel_to_request_ids(tmp_path, monkeypatch):
    records = [
        json.dumps(
            {
                "type": "assistant",
                "requestId": f"req-{i}",
                "message": {"content": [{"type": "tool_use", "name": f"Tool{i}"}]},
            }
        )
        for i in range(3)
    ]
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text("\n".join(records) + "\n")
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert len(summary["turn_tool_calls"]) == len(summary["request_ids"]) == 3


# ---------------------------------------------------------------------------
# Silent gap, outcome anomaly, and exit snapshot tests
# ---------------------------------------------------------------------------


def test_summary_includes_silent_gap_seconds(tmp_path, monkeypatch):
    """silent_gap_seconds computed from cc_log mtime vs end_ts — approx 5.0s."""
    import autoskillit.execution.session_log as sl_mod

    cb_log = tmp_path / "session.jsonl"
    cb_log.write_text("")
    end_ts = "2026-04-15T07:00:10+00:00"
    end_dt = datetime.fromisoformat(end_ts)
    os.utime(cb_log, (end_dt.timestamp() - 5.0,) * 2)
    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    _flush(tmp_path, session_id="gap-test", end_ts=end_ts, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "gap-test" / "summary.json").read_text())
    assert "silent_gap_seconds" in summary
    assert summary["silent_gap_seconds"] == pytest.approx(5.0, abs=0.5)


def test_summary_silent_gap_seconds_null_when_no_end_ts(tmp_path, monkeypatch):
    """silent_gap_seconds is null when end_ts is not provided."""
    import autoskillit.execution.session_log as sl_mod

    cb_log = tmp_path / "session.jsonl"
    cb_log.write_text("")
    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    _flush(tmp_path, session_id="no-end-ts", end_ts="", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "no-end-ts" / "summary.json").read_text())
    assert summary["silent_gap_seconds"] is None


def test_summary_silent_gap_seconds_null_when_cc_log_missing(tmp_path):
    """silent_gap_seconds is null when claude_code_log cannot be resolved."""
    _flush(
        tmp_path,
        session_id="no-cc-log",
        end_ts="2026-04-15T07:00:10+00:00",
        proc_snapshots=None,
        cwd="/nonexistent/path",
    )
    summary = json.loads((tmp_path / "sessions" / "no-cc-log" / "summary.json").read_text())
    assert summary["silent_gap_seconds"] is None


def test_flush_outcome_anomaly_included_in_anomaly_count(tmp_path, monkeypatch):
    """empty_result + output_tokens > 0 increments anomaly_count in summary and index."""
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: None)
    _flush(
        tmp_path,
        session_id="outcome-anomaly",
        subtype="empty_result",
        success=False,
        token_usage={"output_tokens": 945, "input_tokens": 500},
        proc_snapshots=None,
    )
    summary = json.loads((tmp_path / "sessions" / "outcome-anomaly" / "summary.json").read_text())
    assert summary["anomaly_count"] >= 1
    anomalies_path = tmp_path / "sessions" / "outcome-anomaly" / "anomalies.jsonl"
    assert anomalies_path.exists()
    kinds = [json.loads(line)["kind"] for line in anomalies_path.read_text().splitlines() if line]
    assert "empty_result_with_tokens" in kinds
    index_entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert index_entry["anomaly_count"] >= 1


def test_proc_trace_preserves_exit_snapshot_event(tmp_path):
    """proc_trace.jsonl rows with event='exit_snapshot' preserve the marker."""
    exit_snap = {**_snap(), "event": "exit_snapshot"}
    _flush(
        tmp_path,
        session_id="exit-snap-test",
        proc_snapshots=[_snap(), _snap(), exit_snap],
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "sessions" / "exit-snap-test" / "proc_trace.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[0]["event"] == "snapshot"
    assert rows[1]["event"] == "snapshot"
    assert rows[2]["event"] == "exit_snapshot"


# --- Versions block tests ---

_VERSIONS = {
    "autoskillit_version": "1.2.3",
    "install_type": "local-editable",
    "commit_id": None,
    "claude_code_version": "1.0.5",
    "plugins": [],
    "codex_version": "",
    "codex_plugins": [],
}


def test_summary_json_includes_versions_block(tmp_path):
    _flush(tmp_path, session_id="vs-001", versions=_VERSIONS)
    summary = json.loads((tmp_path / "sessions" / "vs-001" / "summary.json").read_text())
    assert "versions" in summary
    assert summary["versions"]["autoskillit_version"] == "1.2.3"
    assert summary["versions"]["claude_code_version"] == "1.0.5"


def test_summary_json_versions_includes_model_identifier(tmp_path):
    _flush(tmp_path, session_id="vs-002", versions=_VERSIONS, model_identifier="claude-opus-4")
    summary = json.loads((tmp_path / "sessions" / "vs-002" / "summary.json").read_text())
    assert summary["versions"]["model_identifier"] == "claude-opus-4"


def test_summary_json_omits_versions_when_not_passed(tmp_path):
    _flush(tmp_path, session_id="vs-003")
    summary = json.loads((tmp_path / "sessions" / "vs-003" / "summary.json").read_text())
    assert "versions" not in summary


def test_sessions_jsonl_includes_autoskillit_version(tmp_path):
    _flush(tmp_path, session_id="vs-004", versions=_VERSIONS)
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-004")
    assert entry["autoskillit_version"] == "1.2.3"


def test_sessions_jsonl_includes_claude_code_version(tmp_path):
    _flush(tmp_path, session_id="vs-005", versions=_VERSIONS)
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-005")
    assert entry["claude_code_version"] == "1.0.5"


def test_sessions_jsonl_includes_codex_version(tmp_path):
    versions_with_codex = {**_VERSIONS, "codex_version": "0.1.0"}
    _flush(tmp_path, session_id="vs-cdx-001", versions=versions_with_codex)
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-cdx-001")
    assert entry["codex_version"] == "0.1.0"


def test_sessions_jsonl_autoskillit_version_empty_when_no_versions(tmp_path):
    _flush(tmp_path, session_id="vs-006")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-006")
    assert entry["autoskillit_version"] == ""


def test_session_log_includes_recipe_name(tmp_path):
    _flush(tmp_path, session_id="rp-001", recipe_name="impl")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-001")
    assert entry["recipe_name"] == "impl"


def test_session_log_includes_recipe_hashes(tmp_path):
    _flush(
        tmp_path,
        session_id="rp-002",
        recipe_content_hash="sha256:abc",
        recipe_composite_hash="sha256:def",
    )
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-002")
    assert entry["recipe_content_hash"] == "sha256:abc"
    assert entry["recipe_composite_hash"] == "sha256:def"


def test_summary_includes_recipe_provenance(tmp_path):
    _flush(
        tmp_path,
        session_id="rp-003",
        recipe_name="impl",
        recipe_content_hash="sha256:abc",
        recipe_composite_hash="sha256:def",
        recipe_version="1.0.0",
    )
    session_dir = tmp_path / "sessions" / "rp-003"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert "recipe_provenance" in summary
    assert summary["recipe_provenance"]["schema_version"] == 1
    assert summary["recipe_provenance"]["name"] == "impl"
    assert summary["recipe_provenance"]["content_hash"] == "sha256:abc"
    assert summary["recipe_provenance"]["composite_hash"] == "sha256:def"
    assert summary["recipe_provenance"]["version"] == "1.0.0"


def test_session_log_empty_recipe_identity(tmp_path):
    _flush(tmp_path, session_id="rp-004")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-004")
    assert entry["recipe_name"] == ""
    assert entry["recipe_content_hash"] == ""
    assert entry["recipe_composite_hash"] == ""


def test_summary_no_recipe_provenance_when_empty(tmp_path):
    _flush(tmp_path, session_id="rp-005")
    session_dir = tmp_path / "sessions" / "rp-005"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert "recipe_provenance" not in summary


def test_flush_index_includes_duration_seconds(tmp_path):
    """sessions.jsonl index entry includes duration_seconds."""
    _flush(tmp_path, elapsed_seconds=42.5)
    index = (tmp_path / "sessions.jsonl").read_text().strip()
    entry = json.loads(index)
    assert entry["duration_seconds"] == pytest.approx(42.5)


# ---------------------------------------------------------------------------
# Provider field tests
# ---------------------------------------------------------------------------


def test_flush_session_log_provider_used_in_summary(tmp_path):
    _flush(tmp_path, session_id="prov-sum", provider_used="minimax", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "prov-sum" / "summary.json").read_text())
    assert summary["provider_used"] == "minimax"


def test_flush_session_log_provider_fallback_in_summary(tmp_path):
    _flush(tmp_path, session_id="fb-sum", provider_fallback=True, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "fb-sum" / "summary.json").read_text())
    assert summary["provider_fallback"] is True


def test_flush_session_log_provider_used_defaults_empty_in_summary(tmp_path):
    _flush(tmp_path, session_id="prov-def", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "prov-def" / "summary.json").read_text())
    assert summary["provider_used"] == ""


def test_flush_session_log_provider_fallback_defaults_false_in_summary(tmp_path):
    _flush(tmp_path, session_id="fb-def", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "fb-def" / "summary.json").read_text())
    assert summary["provider_fallback"] is False


def test_flush_session_log_provider_used_in_index(tmp_path):
    _flush(tmp_path, session_id="prov-idx", provider_used="openai", proc_snapshots=None)
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["provider_used"] == "openai"


def test_flush_session_log_provider_fallback_in_index(tmp_path):
    _flush(tmp_path, session_id="fb-idx", provider_fallback=True, proc_snapshots=None)
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["provider_fallback"] is True


def test_flush_session_log_kill_reason_absent_from_index(tmp_path):
    _flush(tmp_path, session_id="kr-idx", kill_reason="timeout", proc_snapshots=None)
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert "kill_reason" not in entry


def test_flush_session_log_provider_used_in_token_usage(tmp_path):
    _flush(
        tmp_path,
        session_id="prov-tu",
        step_name="implement",
        provider_used="minimax",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        proc_snapshots=None,
    )
    tu = json.loads((tmp_path / "sessions" / "prov-tu" / "token_usage.json").read_text())
    assert tu["provider_used"] == "minimax"


def test_flush_session_log_provider_fallback_absent_from_token_usage(tmp_path):
    _flush(
        tmp_path,
        session_id="fb-tu",
        step_name="implement",
        provider_fallback=True,
        token_usage={"input_tokens": 100, "output_tokens": 50},
        proc_snapshots=None,
    )
    tu = json.loads((tmp_path / "sessions" / "fb-tu" / "token_usage.json").read_text())
    assert "provider_fallback" not in tu


def test_flush_session_log_provider_used_defaults_empty_in_token_usage(tmp_path):
    _flush(
        tmp_path,
        session_id="prov-tu-def",
        step_name="implement",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        proc_snapshots=None,
    )
    tu = json.loads((tmp_path / "sessions" / "prov-tu-def" / "token_usage.json").read_text())
    assert tu["provider_used"] == ""


def test_turn_tool_calls_merged_across_thinking_and_tool_records(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            request_id="req-001",
            timestamp="2026-05-04T00:00:00Z",
            content=[_make_thinking_block()],
        )
        + "\n"
        + _make_cc_jsonl_record(
            request_id="req-001",
            content=[_make_tool_block("Bash")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-04T00:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["turn_tool_calls"] == [["Bash"]]
    assert summary["request_ids"] == ["req-001"]


def test_parallel_lists_aligned_when_timestamp_missing(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            request_id="req-001",
            timestamp="2026-05-04T00:00:00Z",
            content=[_make_tool_block("Read")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            request_id="req-002",
            content=[_make_tool_block("Edit")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-04T00:00:00Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert (
        len(summary["request_ids"])
        == len(summary["turn_timestamps"])
        == len(summary["turn_tool_calls"])
        == 2
    )
    assert summary["request_ids"] == ["req-001", "req-002"]
    assert summary["turn_timestamps"][0] == "2026-05-04T00:00:00Z"
    assert summary["turn_timestamps"][1] == ""
    assert summary["turn_tool_calls"] == [["Read"], ["Edit"]]


def test_flush_index_includes_caller_session_id(tmp_path):
    """sessions.jsonl index entry includes caller_session_id when provided."""
    _flush(tmp_path, session_id="caller-test", caller_session_id="parent-abc")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["caller_session_id"] == "parent-abc"


def test_flush_index_caller_session_id_defaults_to_empty(tmp_path):
    """caller_session_id defaults to empty string in sessions.jsonl index entry."""
    _flush(tmp_path, session_id="caller-default")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["caller_session_id"] == ""


def test_flush_session_log_provider_used_defaults_to_empty_string(tmp_path):
    _flush(tmp_path, session_id="prov-def-001", proc_snapshots=None)
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["provider_used"] == ""


def test_flush_session_log_provider_fallback_defaults_to_false(tmp_path):
    _flush(tmp_path, session_id="prov-def-002", proc_snapshots=None)
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
    assert entry["provider_fallback"] is False


def test_token_usage_json_includes_model_identifier(tmp_path):
    """flush_session_log writes model_identifier to token_usage.json."""
    _flush(
        tmp_path,
        session_id="model-id-001",
        proc_snapshots=None,
        token_usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "model_breakdown": {"claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 50}},
        },
    )
    tu_path = tmp_path / "sessions" / "model-id-001" / "token_usage.json"
    assert tu_path.exists()
    data = json.loads(tu_path.read_text())
    assert "model_identifier" in data
    assert data["model_identifier"] == "claude-sonnet-4-6"


def test_token_usage_json_model_identifier_empty_when_no_breakdown(tmp_path):
    """flush_session_log writes empty model_identifier when no model_breakdown."""
    _flush(
        tmp_path,
        session_id="model-id-002",
        proc_snapshots=None,
        token_usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        },
    )
    tu_path = tmp_path / "sessions" / "model-id-002" / "token_usage.json"
    data = json.loads(tu_path.read_text())
    assert data.get("model_identifier", "") == ""


class TestApiRetryFields:
    """T8: api_retry fields written to summary.json, sessions.jsonl, and anomalies.jsonl."""

    def test_api_retry_fields_in_summary(self, tmp_path):
        """summary.json includes api_retry_count, api_retry_last_error, api_retry_last_status."""
        _flush(
            tmp_path,
            session_id="retry-summary",
            api_retry_count=5,
            api_retry_last_error="overloaded",
            api_retry_last_status=529,
            api_retry_exhausted=True,
            proc_snapshots=None,
        )
        summary = json.loads(
            (tmp_path / "sessions" / "retry-summary" / "summary.json").read_text()
        )
        assert summary["api_retry_count"] == 5
        assert summary["api_retry_last_error"] == "overloaded"
        assert summary["api_retry_last_status"] == 529
        assert summary["api_retry_exhausted"] is True

    def test_api_retry_fields_in_index(self, tmp_path):
        """sessions.jsonl includes api_retry_count, api_retry_exhausted, api_retry_last_error, and api_retry_last_status."""
        _flush(
            tmp_path,
            session_id="retry-index",
            api_retry_count=3,
            api_retry_last_error="unknown",
            api_retry_last_status=None,
            api_retry_exhausted=True,
            proc_snapshots=None,
        )
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip().split("\n")[-1])
        assert entry["api_retry_count"] == 3
        assert entry["api_retry_exhausted"] is True
        assert entry["api_retry_last_error"] == "unknown"
        assert entry["api_retry_last_status"] is None

    def test_api_retry_fields_default_zero_in_summary(self, tmp_path):
        """flush_session_log without api_retry params produces zero/false defaults."""
        _flush(tmp_path, session_id="retry-defaults", proc_snapshots=None)
        summary = json.loads(
            (tmp_path / "sessions" / "retry-defaults" / "summary.json").read_text()
        )
        assert summary["api_retry_count"] == 0
        assert summary["api_retry_exhausted"] is False

    def test_api_retry_exhaustion_anomaly_fires_without_token_usage(self, tmp_path):
        """API_RETRY_EXHAUSTION anomaly fires even when token_usage=None."""
        _flush(
            tmp_path,
            session_id="retry-anomaly",
            api_retry_count=10,
            api_retry_last_error="overloaded",
            api_retry_last_status=529,
            api_retry_exhausted=True,
            token_usage=None,
            proc_snapshots=None,
        )
        anomaly_path = tmp_path / "sessions" / "retry-anomaly" / "anomalies.jsonl"
        anomalies = [json.loads(line) for line in anomaly_path.read_text().strip().split("\n")]
        api_retry_anomalies = [a for a in anomalies if a["kind"] == "api_retry_exhaustion"]
        assert len(api_retry_anomalies) == 1
        assert api_retry_anomalies[0]["detail"]["api_retry_count"] == 10


class TestCodexLogFields:
    """T5: codex_log_path parameter stores codex_log in sessions index."""

    def test_flush_stores_codex_log_in_sessions_index(self, tmp_path):
        codex_log = tmp_path / "codex-sessions" / "2026" / "05" / "26" / "rollout.jsonl"
        codex_log.parent.mkdir(parents=True)
        codex_log.write_text('{"type":"thread.started","thread_id":"tid"}\n')
        flush_session_log(
            log_dir=str(tmp_path),
            codex_log_path=codex_log,
            cwd="/some/worktree",
            session_id="codex-session-001",
            pid=12345,
            skill_command="/autoskillit:implement",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-05-26T08:00:00",
            proc_snapshots=None,
            telemetry=SessionTelemetry.empty(),
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
        )
        index = (tmp_path / "sessions.jsonl").read_text().strip()
        entry = json.loads(index)
        assert entry["codex_log"] == str(codex_log)
        assert entry["claude_code_log"] is None

    def test_flush_codex_log_null_when_not_provided(self, tmp_path):
        flush_session_log(
            log_dir=str(tmp_path),
            cwd="/some/worktree",
            session_id="cc-session-001",
            pid=12345,
            skill_command="/autoskillit:implement",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-05-26T08:00:00",
            proc_snapshots=None,
            telemetry=SessionTelemetry.empty(),
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
        )
        index = (tmp_path / "sessions.jsonl").read_text().strip()
        entry = json.loads(index)
        assert entry["codex_log"] is None

    def test_flush_codex_log_skips_claude_code_log_not_found_warning(self, tmp_path, caplog):
        codex_log = tmp_path / "rollout.jsonl"
        codex_log.write_text('{"type":"thread.started","thread_id":"tid"}\n')
        flush_session_log(
            log_dir=str(tmp_path),
            codex_log_path=codex_log,
            cwd="/some/worktree",
            session_id="codex-session-002",
            pid=12345,
            skill_command="/autoskillit:implement",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-05-26T08:00:00",
            proc_snapshots=None,
            telemetry=SessionTelemetry.empty(),
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
        )
        assert "claude_code_log_not_found" not in caplog.text


def test_primary_model_identifier_parent_wins_on_output_tokens():
    """_primary_model_identifier returns the model with the most output tokens,
    resisting subagent input/cache volume dominance (parent wins case)."""
    from autoskillit.execution.session_log import _primary_model_identifier

    token_usage = {
        "model_breakdown": {
            "claude-opus-4-6": {
                "input_tokens": 5000,
                "output_tokens": 12000,
                "cache_read_tokens": 1000,
                "cache_write_tokens": 500,
            },
            "claude-sonnet-4-6": {
                "input_tokens": 80000,
                "output_tokens": 3000,
                "cache_read_tokens": 60000,
                "cache_write_tokens": 2000,
            },
        }
    }
    result = _primary_model_identifier(token_usage)
    assert result == "claude-opus-4-6"


def test_primary_model_identifier_argmax_parent_only():
    """After subagent filtering, model_breakdown contains only parent models.
    Argmax returns the parent model with the highest output_tokens."""
    from autoskillit.execution.session_log import _primary_model_identifier

    token_usage = {
        "model_breakdown": {
            "claude-opus-4-6": {
                "input_tokens": 50000,
                "output_tokens": 8000,
            },
        }
    }
    result = _primary_model_identifier(token_usage)
    assert result == "claude-opus-4-6"


def test_no_false_drift_with_subagent_dominant_output():
    """End-to-end: opus parent + sonnet subagent stream -> no MODEL_DRIFT anomaly."""
    from autoskillit.execution.anomaly_detection import detect_model_drift
    from autoskillit.execution.session import extract_token_usage
    from autoskillit.execution.session_log import _primary_model_identifier

    parent_lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-6",
                    "usage": {"input_tokens": 1000, "output_tokens": 5000},
                },
            }
        ),
    ] * 10
    subagent_lines = [
        json.dumps(
            {
                "type": "assistant",
                "subagent_type": "Explore",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 500, "output_tokens": 10000},
                },
            }
        ),
    ] * 20
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "test",
            "errors": [],
        }
    )
    stdout = "\n".join(parent_lines + subagent_lines + [result_line])
    token_usage = extract_token_usage(stdout)
    observed = _primary_model_identifier(token_usage)
    assert observed == "claude-opus-4-6"
    anomalies = detect_model_drift("claude-opus-4-6[1m]", observed)
    assert anomalies == []


def test_flush_session_log_configured_model_written_to_token_usage(tmp_path):
    """model_identifier is written to token_usage.json regardless of argmax."""
    _flush(
        tmp_path,
        session_id="configured-model-tu-001",
        proc_snapshots=None,
        model_identifier="claude-opus-4-6",
        token_usage={
            "input_tokens": 25000,
            "output_tokens": 10000,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "model_breakdown": {
                "claude-sonnet-4-6": {"input_tokens": 20000, "output_tokens": 8000},
                "claude-opus-4-6": {"input_tokens": 5000, "output_tokens": 2000},
            },
        },
    )
    tu_path = tmp_path / "sessions" / "configured-model-tu-001" / "token_usage.json"
    assert tu_path.exists()
    data = json.loads(tu_path.read_text())
    assert data["model_identifier"] == "claude-opus-4-6"
    assert data["configured_model"] == "claude-opus-4-6"


def test_flush_session_log_no_false_drift_with_configured_model(tmp_path):
    """When model_identifier matches the dominant model in model_breakdown, no drift anomaly fires.

    The configured and API-observed models genuinely agree.
    """
    _flush(
        tmp_path,
        session_id="no-false-drift-001",
        proc_snapshots=None,
        model_identifier="claude-opus-4-6",
        token_usage={
            "model_breakdown": {
                "claude-opus-4-6": {"input_tokens": 20000, "output_tokens": 8000},
            },
        },
    )
    anomalies_path = tmp_path / "sessions" / "no-false-drift-001" / "anomalies.jsonl"
    drift_entries = []
    if anomalies_path.exists():
        anomaly_lines = anomalies_path.read_text().strip().splitlines()
        drift_entries = [
            json.loads(line)
            for line in anomaly_lines
            if json.loads(line).get("kind") == "model_drift"
        ]
    assert len(drift_entries) == 0


def test_flush_session_log_bracket_suffix_no_drift(tmp_path):
    """configured_model='opus[1m]' must not cause drift against claude-opus-4-6."""
    _flush(
        tmp_path,
        session_id="bracket-suffix-001",
        proc_snapshots=None,
        model_identifier="opus[1m]",
        token_usage={
            "model_breakdown": {
                "claude-opus-4-6": {"input_tokens": 5000, "output_tokens": 12000},
            },
        },
    )
    anomalies_path = tmp_path / "sessions" / "bracket-suffix-001" / "anomalies.jsonl"
    drift = []
    if anomalies_path.exists():
        lines = anomalies_path.read_text().strip().splitlines()
        drift = [json.loads(ln) for ln in lines if json.loads(ln).get("kind") == "model_drift"]
    assert len(drift) == 0


def test_flush_session_log_genuine_drift_with_configured_model(tmp_path):
    """When model_identifier differs from the dominant model in model_breakdown, a MODEL_DRIFT anomaly fires."""
    _flush(
        tmp_path,
        session_id="genuine-drift-001",
        proc_snapshots=None,
        model_identifier="claude-opus-4-6",
        token_usage={
            "model_breakdown": {
                "claude-sonnet-4-6": {"input_tokens": 20000, "output_tokens": 8000},
                "claude-opus-4-6": {"input_tokens": 1000, "output_tokens": 200},
            },
        },
    )
    anomalies_path = tmp_path / "sessions" / "genuine-drift-001" / "anomalies.jsonl"
    assert anomalies_path.exists(), "anomalies.jsonl must be written when drift is detected"
    all_entries = [json.loads(line) for line in anomalies_path.read_text().strip().splitlines()]
    drift_entries = [e for e in all_entries if e.get("kind") == "model_drift"]
    assert len(drift_entries) == 1, (
        f"expected 1 model_drift entry, got {len(drift_entries)}: {drift_entries}"
    )
    assert drift_entries[0]["detail"]["configured_model"] == "claude-opus-4-6"
    assert drift_entries[0]["detail"]["observed_model"] == "claude-sonnet-4-6"


def test_flush_session_log_argmax_fallback_prefers_output_tokens(tmp_path):
    """When model_identifier is empty, flush_session_log uses output-token-weighted
    argmax, not total-token argmax."""
    _flush(
        tmp_path,
        session_id="argmax-fallback-001",
        proc_snapshots=None,
        model_identifier="",
        token_usage={
            "input_tokens": 85000,
            "output_tokens": 15000,
            "cache_write_tokens": 2500,
            "cache_read_tokens": 61000,
            "model_breakdown": {
                "claude-opus-4-6": {
                    "input_tokens": 5000,
                    "output_tokens": 12000,
                    "cache_read_tokens": 1000,
                    "cache_write_tokens": 500,
                },
                "claude-sonnet-4-6": {
                    "input_tokens": 80000,
                    "output_tokens": 3000,
                    "cache_read_tokens": 60000,
                    "cache_write_tokens": 2000,
                },
            },
        },
    )
    tu_path = tmp_path / "sessions" / "argmax-fallback-001" / "token_usage.json"
    assert tu_path.exists()
    data = json.loads(tu_path.read_text())
    assert data["model_identifier"] == "claude-opus-4-6"


class TestSkillCommandPersistenceFidelity:
    """skill_command must be stored without truncation in both sessions.jsonl and summary.json."""

    _LONG_COMMAND = "/autoskillit:implement " + "x" * 230

    def test_long_skill_command_survives_in_index(self, tmp_path):
        """A 250-char skill_command written to sessions.jsonl is stored intact."""
        _flush(
            tmp_path,
            session_id="fidelity-index",
            skill_command=self._LONG_COMMAND,
            proc_snapshots=None,
        )
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        assert entry["skill_command"] == self._LONG_COMMAND

    def test_long_skill_command_survives_in_summary(self, tmp_path):
        """A 250-char skill_command written to summary.json is stored intact."""
        _flush(
            tmp_path,
            session_id="fidelity-summary",
            skill_command=self._LONG_COMMAND,
            proc_snapshots=None,
        )
        summary = json.loads(
            (tmp_path / "sessions" / "fidelity-summary" / "summary.json").read_text()
        )
        assert summary["skill_command"] == self._LONG_COMMAND

    def test_index_and_summary_skill_command_are_identical(self, tmp_path):
        """skill_command in sessions.jsonl and summary.json must be identical."""
        _flush(
            tmp_path,
            session_id="fidelity-consist",
            skill_command=self._LONG_COMMAND,
            proc_snapshots=None,
        )
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        summary = json.loads(
            (tmp_path / "sessions" / "fidelity-consist" / "summary.json").read_text()
        )
        assert entry["skill_command"] == summary["skill_command"]


class TestSessionIndexSummaryConsistency:
    """Overlapping fields in sessions.jsonl and summary.json must have identical values."""

    _OVERLAP_FIELDS = {
        "session_id",
        "cwd",
        "skill_command",
        "success",
        "subtype",
        "exit_code",
        "snapshot_count",
        "anomaly_count",
        "write_call_count",
        "fs_writes_detected",
        "git_writes_detected",
        "file_changes_count",
        "github_api_requests",
        "api_retry_count",
        "api_retry_exhausted",
        "api_retry_last_error",
        "api_retry_last_status",
        "caller_session_id",
    }

    def test_overlapping_fields_are_consistent(self, tmp_path):
        """All fields present in both index and summary must have identical values."""
        _flush(
            tmp_path,
            session_id="consist-check",
            skill_command="/autoskillit:implement " + "y" * 200,
            api_retry_count=2,
            api_retry_last_error="overloaded",
            api_retry_last_status=529,
            api_retry_exhausted=False,
            write_call_count=7,
            proc_snapshots=None,
        )
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        summary = json.loads(
            (tmp_path / "sessions" / "consist-check" / "summary.json").read_text()
        )
        for field in self._OVERLAP_FIELDS:
            assert field in entry, f"Field '{field}' missing from sessions.jsonl index entry"
            assert field in summary, f"Field '{field}' missing from summary.json"
            assert entry[field] == summary[field], (
                f"Field '{field}' differs: index={entry[field]!r}, summary={summary[field]!r}"
            )


class TestModelAliasDriftIntegration:
    """Integration tests: alias normalization and profile_name in flush_session_log."""

    def test_alias_configured_full_observed_no_drift(self, tmp_path):
        """Alias 'sonnet' vs API-returned 'claude-sonnet-4-6' should produce no MODEL_DRIFT."""
        _flush(
            tmp_path,
            session_id="alias-no-drift-001",
            proc_snapshots=None,
            model_identifier="sonnet",
            token_usage={
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "model_breakdown": {
                    "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500},
                },
            },
        )
        anomalies_path = tmp_path / "sessions" / "alias-no-drift-001" / "anomalies.jsonl"
        lines = anomalies_path.read_text().strip().splitlines() if anomalies_path.exists() else []
        drift = [
            json.loads(line) for line in lines if json.loads(line).get("kind") == "model_drift"
        ]
        assert len(drift) == 0

    def test_profile_name_recorded_in_sessions_jsonl(self, tmp_path):
        """profile_name is written to sessions.jsonl index entry."""
        _flush(
            tmp_path,
            session_id="profile-record-001",
            proc_snapshots=None,
            profile_name="minimax",
        )
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        assert entry["profile_name"] == "minimax"

    def test_profile_name_recorded_in_token_usage(self, tmp_path):
        """profile_name is written to token_usage.json."""
        _flush(
            tmp_path,
            session_id="profile-tu-001",
            proc_snapshots=None,
            step_name="step-with-profile",
            profile_name="minimax",
            token_usage={"input_tokens": 100, "output_tokens": 50},
        )
        tu = json.loads(
            (tmp_path / "sessions" / "profile-tu-001" / "token_usage.json").read_text()
        )
        assert tu["profile_name"] == "minimax"

    def test_rss_startup_artifact_suppressed_through_flush(self, tmp_path):
        """5MB startup RSS → 270MB working set should produce no RSS_GROWTH via flush."""
        snaps = [
            _snap(vm_rss_kb=5000),
            _snap(vm_rss_kb=50000),
            _snap(vm_rss_kb=150000),
            _snap(vm_rss_kb=270000),
            _snap(vm_rss_kb=280000),
        ]
        _flush(tmp_path, session_id="rss-startup-flush-001", proc_snapshots=snaps)
        anomalies_path = tmp_path / "sessions" / "rss-startup-flush-001" / "anomalies.jsonl"
        lines = anomalies_path.read_text().strip().splitlines() if anomalies_path.exists() else []
        rss = [json.loads(line) for line in lines if json.loads(line).get("kind") == "rss_growth"]
        assert len(rss) == 0


def test_flush_session_log_minimax_message_id_turn_dedup(tmp_path, monkeypatch):
    """MiniMax-shaped JSONL (message.id, no requestId) deduplicates into correct turn count."""
    mid = "0669d3ed14adce24ccf227c37a5884d4"
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        _make_cc_jsonl_record(
            message_id=mid,
            timestamp="2026-05-30T08:33:53.843Z",
            content=[_make_thinking_block("reasoning...")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            message_id=mid,
            timestamp="2026-05-30T08:33:54.810Z",
            content=[_make_tool_block("Bash")],
        )
        + "\n"
        + _make_cc_jsonl_record(
            message_id="aabbccddeeff00112233445566778899",
            timestamp="2026-05-30T08:34:01.000Z",
            content=[_make_tool_block("Read")],
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="minimax-dedup-001",
        pid=1,
        skill_command="test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-05-30T08:33:53.843Z",
        proc_snapshots=None,
        telemetry=SessionTelemetry.empty(),
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
    )
    summary = json.loads(
        (tmp_path / "sessions" / "minimax-dedup-001" / "summary.json").read_text()
    )
    assert summary["request_ids"] == [mid, "aabbccddeeff00112233445566778899"]
    assert len(summary["turn_timestamps"]) == 2
    assert len(summary["turn_tool_calls"]) == 2
    assert summary["turn_timestamps"][0] == "2026-05-30T08:33:53.843Z"

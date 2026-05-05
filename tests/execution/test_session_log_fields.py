"""Tests for flush_session_log field coverage: write warnings, call counts,
kitchen/order IDs, crash exception, raw stdout, per-turn fields, tool calls,
silent gap, proc-trace exit snapshot, versions block, and recipe identity."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from autoskillit.core.types._type_results import SessionTelemetry
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
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["last_stop_reason"] == "end_turn"
    assert summary["request_ids"] == ["req-001", "req-002"]
    assert summary["turn_timestamps"] == ["2026-04-15T07:00:00Z", "2026-04-15T07:00:05Z"]


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
    assert summary["recipe_provenance"]["recipe_name"] == "impl"
    assert summary["recipe_provenance"]["content_hash"] == "sha256:abc"
    assert summary["recipe_provenance"]["composite_hash"] == "sha256:def"
    assert summary["recipe_provenance"]["recipe_version"] == "1.0.0"


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


# ---------------------------------------------------------------------------
# Extended-thinking merge tests (1d, 1e — currently failing)
# ---------------------------------------------------------------------------


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
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert (
        len(summary["request_ids"])
        == len(summary["turn_timestamps"])
        == len(summary["turn_tool_calls"])
        == 2
    )
    assert summary["turn_timestamps"][1] == ""

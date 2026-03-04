"""Tests for the session diagnostics log writer."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.execution.session_log import flush_session_log, resolve_log_dir


def _snap(
    *,
    vm_rss_kb: int = 100000,
    oom_score: int = 50,
    fd_count: int = 10,
    fd_soft_limit: int = 1024,
    state: str = "sleeping",
) -> dict[str, object]:
    return {
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
    defaults = {
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
    }
    defaults.update(overrides)
    flush_session_log(**defaults)


def test_flush_session_log_creates_directory_structure(tmp_path):
    """Flush creates the expected directory structure."""
    _flush(tmp_path)
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert session_dir.is_dir()
    assert (session_dir / "proc_trace.jsonl").is_file()
    assert (session_dir / "summary.json").is_file()
    assert (tmp_path / "sessions.jsonl").is_file()


def test_flush_session_log_writes_proc_trace_as_jsonl(tmp_path):
    """proc_trace.jsonl has exactly 3 lines, each valid JSON."""
    _flush(tmp_path)
    trace_path = tmp_path / "sessions" / "test-session-001" / "proc_trace.jsonl"
    lines = trace_path.read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["seq"] == i
        assert record["event"] == "snapshot"
        assert record["pid"] == 12345
        assert "vm_rss_kb" in record


def test_flush_session_log_writes_summary_json(tmp_path):
    """summary.json contains expected session metadata."""
    _flush(tmp_path)
    summary_path = tmp_path / "sessions" / "test-session-001" / "summary.json"
    summary = json.loads(summary_path.read_text())
    assert summary["session_id"] == "test-session-001"
    assert summary["pid"] == 12345
    assert summary["success"] is True
    assert summary["snapshot_count"] == 3
    assert summary["cwd"] == "/home/test/project"
    assert "peak_rss_kb" in summary
    assert "peak_oom_score" in summary


def test_flush_session_log_creates_anomalies_file_only_when_anomalies_exist(tmp_path):
    """anomalies.jsonl is only created when anomalies are detected."""
    # Normal snapshots — no anomalies
    _flush(tmp_path, session_id="normal-session")
    anomalies_path = tmp_path / "sessions" / "normal-session" / "anomalies.jsonl"
    assert not anomalies_path.exists()

    # Snapshots with OOM critical — should create anomalies
    _flush(
        tmp_path,
        session_id="anomaly-session",
        proc_snapshots=[_snap(oom_score=900)],
    )
    anomalies_path = tmp_path / "sessions" / "anomaly-session" / "anomalies.jsonl"
    assert anomalies_path.is_file()
    lines = anomalies_path.read_text().strip().split("\n")
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["kind"] == "oom_critical"


def test_flush_session_log_appends_to_index(tmp_path):
    """Two flushes produce exactly 2 lines in sessions.jsonl."""
    _flush(tmp_path, session_id="session-a")
    _flush(tmp_path, session_id="session-b")
    index_path = tmp_path / "sessions.jsonl"
    lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    assert len(lines) == 2
    a = json.loads(lines[0])
    b = json.loads(lines[1])
    assert a["session_id"] == "session-a"
    assert b["session_id"] == "session-b"


def test_flush_session_log_fallback_dirname_when_no_session_id(tmp_path):
    """Empty session_id falls back to no_session_ prefix with timestamp."""
    _flush(tmp_path, session_id="", pid=99999, start_ts="2026-03-03T12:00:00+00:00")
    sessions_dir = tmp_path / "sessions"
    dirs = list(sessions_dir.iterdir())
    assert len(dirs) == 1
    dir_name = dirs[0].name
    assert dir_name.startswith("no_session_")
    assert "2026" in dir_name


def test_flush_session_log_uses_resolved_session_id(tmp_path):
    """flush_session_log uses the caller-resolved session_id for dir name."""
    _flush(tmp_path, session_id="chan-b-uuid-123")
    session_dir = tmp_path / "sessions" / "chan-b-uuid-123"
    assert session_dir.is_dir()
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["session_id"] == "chan-b-uuid-123"


def test_flush_session_log_handles_empty_snapshots(tmp_path):
    """proc_snapshots=None produces no proc_trace.jsonl but summary is still written."""
    _flush(tmp_path, proc_snapshots=None)
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert not (session_dir / "proc_trace.jsonl").exists()
    assert (session_dir / "summary.json").is_file()
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["snapshot_count"] == 0


def test_flush_session_log_retention_purges_oldest(tmp_path):
    """After 503 sessions, only 500 remain and the 3 oldest are gone."""

    # Create 502 session directories with staggered mtimes
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(502):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        (d / "summary.json").write_text("{}")
        # Set mtime to ensure ordering
        mtime = 1000000000 + i
        import os

        os.utime(d, (mtime, mtime))
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    # Flush a 503rd session
    _flush(tmp_path, session_id="session-0502")

    remaining = list(sessions_dir.iterdir())
    assert len(remaining) == 500

    # The 3 oldest (session-0000, session-0001, session-0002) should be gone
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()
    assert not (sessions_dir / "session-0002").exists()

    # The newest should still be there
    assert (sessions_dir / "session-0502").exists()

    # Index should only contain entries for surviving sessions
    index_lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    assert len(index_lines) == 500


# --- resolve_log_dir tests ---


def test_resolve_log_dir_default_linux(monkeypatch):
    """Empty log_dir on Linux (no XDG_DATA_HOME) uses ~/.local/share/autoskillit/logs."""
    monkeypatch.setattr("autoskillit.execution.session_log.sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = resolve_log_dir("")
    assert result == Path.home() / ".local" / "share" / "autoskillit" / "logs"


def test_resolve_log_dir_xdg_override(monkeypatch):
    """XDG_DATA_HOME override is respected."""
    monkeypatch.setattr("autoskillit.execution.session_log.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
    result = resolve_log_dir("")
    assert result == Path("/custom/xdg/autoskillit/logs")


def test_resolve_log_dir_explicit_override():
    """Explicit log_dir is used as-is."""
    result = resolve_log_dir("/custom/path")
    assert result == Path("/custom/path")

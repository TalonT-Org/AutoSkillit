"""Contracts for bounded Codex cook startup tracing and PTY observation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_LAUNCH_ID = "0123456789abcdef"
_VIEW_ID = f"{_LAUNCH_ID}-1"
_RECORD_LIMIT = 16 * 1024
_WINDOW_LIMIT = 64 * 1024


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _trace_module():
    from autoskillit.cli.session import _session_startup_trace

    return _session_startup_trace


def _observer_api():
    from autoskillit.cli.session.pty._observer import (
        CodexStateReadinessProbe,
        ObserverStatus,
        PtyObserver,
    )

    return CodexStateReadinessProbe, ObserverStatus, PtyObserver


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "launch_id",
    [
        "",
        "0123456789abcde",
        "0123456789abcdef0",
        "0123456789ABCDEf",
        "../1234567890abc",
        "01234567/90abcde",
        "/1234567890abcde",
    ],
)
def test_startup_trace_path_rejects_noncanonical_launch_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launch_id: str
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")

    with pytest.raises(ValueError):
        trace_mod.startup_trace_path(project, launch_id)


def test_startup_trace_path_is_the_project_keyed_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    log_root = tmp_path / "logs"
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: log_root)

    path = trace_mod.startup_trace_path(project, _LAUNCH_ID)
    project_key = hashlib.sha256(os.fsencode(str(project.resolve(strict=True)))).hexdigest()[:16]

    assert path == log_root / "codex-startup" / project_key / f"{_LAUNCH_ID}.jsonl"


def test_startup_trace_path_rejects_a_symlinked_parent_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    log_root = tmp_path / "logs"
    trace_root = log_root / "codex-startup"
    trace_root.mkdir(parents=True)
    project_key = hashlib.sha256(os.fsencode(str(project.resolve(strict=True)))).hexdigest()[:16]
    (trace_root / project_key).symlink_to(tmp_path / "outside", target_is_directory=True)
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: log_root)

    with pytest.raises((OSError, ValueError)):
        trace_mod.startup_trace_path(project, _LAUNCH_ID)


def test_trace_schema_anchors_monotonic_math_budgets_and_history_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    clock = _Clock()
    trace = trace_mod.StartupTrace(
        project_dir=project,
        launch_id=_LAUNCH_ID,
        enabled=True,
        clock=clock,
    )

    trace.record_launch_anchor()
    trace.record_attempt_anchor(
        attempt=1,
        view_id=_VIEW_ID,
        diagnostics={"history_file_count": 500, "history_allocated_bytes": 8_388_608},
    )
    clock.value = 104.5
    trace.record_stage("spawn", attempt=1, view_id=_VIEW_ID)
    clock.value = 105.0
    trace.record_stage("state_ready", attempt=1, view_id=_VIEW_ID)
    clock.value = 105.25
    trace.record_stage("first_output", attempt=1, view_id=_VIEW_ID)
    clock.value = 116.5
    trace.record_stage("hook_review", attempt=1, view_id=_VIEW_ID)
    trace.require_startup_budgets()
    trace.close(status="success")

    records = _records(trace.path)
    assert {record["schema_version"] for record in records} == {1}
    assert {record["launch_id"] for record in records} == {_LAUNCH_ID}
    assert [record["record_type"] for record in records] == [
        "launch",
        "attempt",
        "stage",
        "stage",
        "stage",
        "stage",
        "summary",
    ]
    assert [record["monotonic_seconds"] for record in records] == sorted(
        record["monotonic_seconds"] for record in records
    )
    assert records[1]["attempt"] == 1
    assert records[1]["view_id"] == _VIEW_ID
    assert records[1]["diagnostics"] == {
        "history_file_count": 500,
        "history_allocated_bytes": 8_388_608,
    }
    summary = records[-1]
    assert summary["status"] == "success"
    assert summary["durations_seconds"] == pytest.approx(
        {
            "confirmation_to_spawn": 4.5,
            "spawn_to_hook_review": 12.0,
            "total_startup": 16.5,
        }
    )
    assert summary["budgets_seconds"] == {
        "confirmation_to_spawn": 5.0,
        "spawn_to_hook_review": 12.0,
        "total_startup": 17.0,
    }
    assert summary["budget_exceeded"] == []
    assert summary["budget_missing"] == []
    assert summary["budgets_passed"] is True


@pytest.mark.parametrize(
    ("spawn_at", "hook_at", "exceeded"),
    [
        (105.001, 116.0, {"confirmation_to_spawn"}),
        (104.0, 116.001, {"spawn_to_hook_review"}),
        (
            105.001,
            117.002,
            {"confirmation_to_spawn", "spawn_to_hook_review", "total_startup"},
        ),
    ],
)
def test_absolute_startup_budgets_are_hard_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawn_at: float,
    hook_at: float,
    exceeded: set[str],
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    clock = _Clock()
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True, clock=clock)
    trace.record_launch_anchor()
    trace.record_attempt_anchor(attempt=1, view_id=_VIEW_ID)
    clock.value = spawn_at
    trace.record_stage("spawn", attempt=1, view_id=_VIEW_ID)
    clock.value = hook_at
    trace.record_stage("hook_review", attempt=1, view_id=_VIEW_ID)
    with pytest.raises(RuntimeError, match="startup budgets failed"):
        trace.require_startup_budgets()
    trace.close(status="failed")

    summary = _records(trace.path)[-1]
    assert set(summary["budget_exceeded"]) == exceeded
    assert summary["budget_missing"] == []
    assert summary["budgets_passed"] is False


def test_missing_spawn_and_hook_stages_fail_budget_enforcement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)
    trace.record_launch_anchor()
    trace.record_attempt_anchor(attempt=1, view_id=_VIEW_ID)

    with pytest.raises(RuntimeError, match="unmeasured="):
        trace.require_startup_budgets()
    trace.close(status="failed")

    summary = _records(trace.path)[-1]
    assert set(summary["budget_missing"]) == {
        "confirmation_to_spawn",
        "spawn_to_hook_review",
        "total_startup",
    }
    assert summary["budgets_passed"] is False


def test_spawn_stage_is_exactly_once_per_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)
    trace.record_launch_anchor()
    trace.record_attempt_anchor(attempt=1, view_id=_VIEW_ID)
    trace.record_spawn()

    with pytest.raises(RuntimeError, match="spawn already recorded"):
        trace.record_spawn()


def test_trace_records_are_individually_capped_and_diagnostics_are_byte_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)
    trace.record_launch_anchor()
    trace.record_attempt_anchor(
        attempt=1,
        view_id=_VIEW_ID,
        diagnostics={"raw_output": "é" * 100_000},
    )
    trace.close(status="error")

    raw_records = trace.path.read_bytes().splitlines(keepends=True)
    assert raw_records
    assert all(len(record) <= _RECORD_LIMIT for record in raw_records)
    assert b"\xc3\xa9" * 100_000 not in b"".join(raw_records)
    assert all(json.loads(record) for record in raw_records)


def test_mandatory_record_overflow_closes_the_trace_with_an_explicit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)
    trace.record_launch_anchor()

    with pytest.raises(RuntimeError, match="overflow|16 KiB"):
        trace.record_stage("mandatory-" + "x" * _RECORD_LIMIT, attempt=1, view_id=_VIEW_ID)

    records = _records(trace.path)
    assert records[-1]["record_type"] == "summary"
    assert records[-1]["status"] == "trace_record_overflow"
    assert all(
        len(line) <= _RECORD_LIMIT for line in trace.path.read_bytes().splitlines(keepends=True)
    )


@pytest.mark.parametrize("status", ["success", "error", "interrupted", "child_failed"])
def test_trace_status_closes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)
    trace.record_launch_anchor()
    trace.close(status=status)
    trace.close(status=status)

    records = _records(trace.path)
    summaries = [record for record in records if record["record_type"] == "summary"]
    assert [summary["status"] for summary in summaries] == [status]
    with pytest.raises(RuntimeError, match="closed|terminal"):
        trace.close(status="success" if status != "success" else "error")


def test_trace_refuses_to_follow_an_existing_trace_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_mod = _trace_module()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(trace_mod, "default_log_dir", lambda: tmp_path / "logs")
    path = trace_mod.startup_trace_path(project, _LAUNCH_ID)
    path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"sentinel\n")
    path.symlink_to(outside)
    trace = trace_mod.StartupTrace(project, _LAUNCH_ID, enabled=True)

    with pytest.raises(OSError):
        trace.record_launch_anchor()
    assert outside.read_bytes() == b"sentinel\n"


def test_observer_relay_is_byte_transparent_and_semantic_matching_is_ansi_normalized() -> None:
    _, _, PtyObserver = _observer_api()
    observer = PtyObserver(readiness_probe=None)
    chunks = [
        b"\x1b[2J\x1b[33m1 hook",
        b"s need rev\x1b[0m",
        b"iew before it can run.\r\n",
    ]

    relayed = b"".join(observer.observe_output(chunk) for chunk in chunks)

    assert relayed == b"".join(chunks)
    assert observer.first_output_seen is True
    assert observer.hook_review_seen is True
    assert "\x1b" not in observer.normalized_window
    assert "hooks need review before it can run" in observer.normalized_window.lower()


def test_observer_matching_window_and_retained_output_are_hard_capped() -> None:
    _, _, PtyObserver = _observer_api()
    observer = PtyObserver(readiness_probe=None)
    payload = b"x" * (_WINDOW_LIMIT + 8192)

    assert observer.observe_output(payload) == payload
    assert len(observer.retained_output) <= _WINDOW_LIMIT
    assert len(observer.normalized_window.encode("utf-8")) <= _WINDOW_LIMIT


def _make_state_db(sqlite_home: Path, status: str = "complete") -> Path:
    sqlite_home.mkdir(parents=True, exist_ok=True)
    path = sqlite_home / "state_5.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE backfill_state (id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO backfill_state (id, status) VALUES (1, ?)", (status,))
    return path


def _probe(sqlite_home: Path):
    CodexStateReadinessProbe, _, _ = _observer_api()
    return CodexStateReadinessProbe(
        codex_version="codex-cli 0.145.0",
        sqlite_home=sqlite_home,
    )


def test_readiness_probe_accepts_only_the_supported_complete_schema(tmp_path: Path) -> None:
    _, ObserverStatus, _ = _observer_api()
    sqlite_home = tmp_path / "sqlite-home"
    _make_state_db(sqlite_home)
    probe = _probe(sqlite_home)

    assert probe.upstream_commit == "ad65f016ed0c91992fb175fa881a373cc460dd2a"
    assert probe.database_path == sqlite_home / "state_5.sqlite"
    assert probe.check() is ObserverStatus.READY


@pytest.mark.parametrize(
    ("fixture", "expected_name"),
    [
        ("absent", "ABSENT"),
        ("corrupt", "CORRUPT"),
        ("incomplete", "INCOMPLETE"),
        ("changed", "SCHEMA_CHANGED"),
    ],
)
def test_readiness_probe_fails_closed_for_unready_state(
    tmp_path: Path, fixture: str, expected_name: str
) -> None:
    _, ObserverStatus, _ = _observer_api()
    sqlite_home = tmp_path / fixture
    sqlite_home.mkdir()
    if fixture == "corrupt":
        (sqlite_home / "state_5.sqlite").write_bytes(b"not sqlite")
    elif fixture == "incomplete":
        _make_state_db(sqlite_home, status="running")
    elif fixture == "changed":
        with sqlite3.connect(sqlite_home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE backfill_state (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO backfill_state (id) VALUES (1)")

    assert _probe(sqlite_home).check() is getattr(ObserverStatus, expected_name)


def test_readiness_probe_reports_a_locked_database_without_waiting(
    tmp_path: Path,
) -> None:
    _, ObserverStatus, _ = _observer_api()
    sqlite_home = tmp_path / "locked"
    path = _make_state_db(sqlite_home)
    owner = sqlite3.connect(path, timeout=0)
    owner.execute("BEGIN EXCLUSIVE")
    try:
        assert _probe(sqlite_home).check() is ObserverStatus.LOCKED
    finally:
        owner.rollback()
        owner.close()


def test_readiness_probe_rejects_unmapped_codex_versions(tmp_path: Path) -> None:
    CodexStateReadinessProbe, ObserverStatus, _ = _observer_api()
    sqlite_home = tmp_path / "sqlite-home"
    _make_state_db(sqlite_home)
    probe = CodexStateReadinessProbe(
        codex_version="codex-cli 0.146.0",
        sqlite_home=sqlite_home,
    )

    assert probe.check() is ObserverStatus.UNSUPPORTED_VERSION


def test_readiness_wait_distinguishes_timeout_and_cancellation(tmp_path: Path) -> None:
    _, ObserverStatus, _ = _observer_api()
    probe = _probe(tmp_path / "absent")

    assert probe.wait(timeout_seconds=0.0) is ObserverStatus.TIMEOUT
    assert probe.wait(timeout_seconds=1.0, cancelled=lambda: True) is ObserverStatus.CANCELLED

"""Tests for recover_crashed_sessions and retention/campaign-protection logic."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoskillit.execution.linux_tracing import read_boot_id, read_starttime_ticks
from autoskillit.execution.session_log import (
    flush_session_log,
    read_telemetry_clear_marker,
    recover_crashed_sessions,
    resolve_log_dir,
    write_telemetry_clear_marker,
)
from autoskillit.fleet import build_protected_campaign_ids

from tests.execution.conftest import _flush, _snap

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


# --- recover_crashed_sessions tests ---


def test_recover_crashed_sessions_noop_when_no_orphans(tmp_path):
    """Returns 0 when tmpfs is empty."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 0


def test_recover_crashed_sessions_noop_when_tmpfs_missing(tmp_path):
    """Returns 0 when tmpfs_path does not exist."""
    count = recover_crashed_sessions(
        tmpfs_path=str(tmp_path / "nonexistent"), log_dir=str(tmp_path / "logs")
    )
    assert count == 0


def test_recover_crashed_sessions_skips_recent_files(tmp_path):
    """Files modified within the last 30 seconds are skipped (may be active)."""

    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = tmpfs / "autoskillit_trace_12345.jsonl"
    trace.write_text(
        json.dumps({"vm_rss_kb": 500, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n"
    )
    # Leave the file fresh (mtime = now)
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 0


def _write_old_trace(tmpfs: Path, filename: str, content: str) -> Path:
    """Write a trace file (backdated 60s) and its enrollment sidecar.

    The enrollment sidecar uses the current boot_id so Gate 2 passes.
    The PID embedded in the filename is expected to be dead (so Gate 3 passes).
    """
    trace = tmpfs / filename
    trace.write_text(content)
    old_mtime = time.time() - 60
    os.utime(trace, (old_mtime, old_mtime))

    # Write companion enrollment sidecar so Gate 1 passes
    try:
        pid = int(Path(filename).stem.split("_")[-1])
    except (ValueError, IndexError):
        pid = 0
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": None,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    return trace


def test_recover_crashed_sessions_finalizes_orphaned_file(tmp_path):
    """recover_crashed_sessions reads tmpfs file and writes permanent session dir."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    _write_old_trace(
        tmpfs,
        "autoskillit_trace_12345.jsonl",
        json.dumps({"vm_rss_kb": 500, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
    )
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 1
    sessions = list((tmp_path / "logs" / "sessions").iterdir())
    assert len(sessions) == 1
    assert "crashed" in sessions[0].name
    assert (sessions[0] / "summary.json").exists()
    summary = json.loads((sessions[0] / "summary.json").read_text())
    assert summary["termination_reason"] == "CRASHED"
    assert summary["success"] is False


def test_recover_crashed_sessions_deletes_tmpfs_file(tmp_path):
    """Trace file is removed from tmpfs after recovery."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = _write_old_trace(
        tmpfs,
        "autoskillit_trace_99999.jsonl",
        json.dumps({"vm_rss_kb": 300, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
    )
    recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert not trace.exists()


def test_recover_crashed_sessions_handles_multiple_files(tmp_path):
    """Multiple orphaned trace files are all recovered."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    for pid in [111, 222, 333]:
        _write_old_trace(
            tmpfs,
            f"autoskillit_trace_{pid}.jsonl",
            json.dumps({"vm_rss_kb": 100, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
        )
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 3
    sessions = list((tmp_path / "logs" / "sessions").iterdir())
    assert len(sessions) == 3


def _write_old_trace_with_comm(tmpfs: Path, pid: int, comm: str, *, n_snaps: int = 2) -> Path:
    """Write a backdated trace file with snapshots that have a specific comm."""
    filename = f"autoskillit_trace_{pid}.jsonl"
    trace = tmpfs / filename
    snaps = []
    for _ in range(n_snaps):
        snap_dict = {**_snap(), "comm": comm}
        snaps.append(json.dumps(snap_dict))
    trace.write_text("\n".join(snaps) + "\n")

    # Backdate so Gate 1 (age > 30s) passes
    old_mtime = time.time() - 60
    os.utime(trace, (old_mtime, old_mtime))

    # Write enrollment sidecar so Gate 1 (sidecar present) passes.
    # Use schema_version=2 with comm='claude' — autoskillit always enrolls its own
    # binary as 'claude'. Snapshots whose first comm != enrollment.comm are alien.
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": None,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
                "comm": "claude",
            }
        )
    )
    return trace


def test_recover_crashed_sessions_excludes_non_claude_trace_files(tmp_path):
    """recover_crashed_sessions tags alien trace files (non-claude comm) and excludes them.

    Test 1.11: place two trace files — one with comm='claude' and one with
    comm='sleep'. The 'sleep' file is an alien artifact (test pollution or a
    non-autoskillit process). After the fix, recovery recognises it via comm
    and either skips it or marks it with alien=true in the recovered record.
    """
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    log_dir = tmp_path / "logs"

    # One legitimate claude trace
    _write_old_trace_with_comm(tmpfs, pid=20001, comm="claude")
    # One alien trace (e.g., leftover from a test or wrong process)
    _write_old_trace_with_comm(tmpfs, pid=20002, comm="sleep")

    recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(log_dir))

    sessions_dir = log_dir / "sessions"
    recovered = list(sessions_dir.iterdir()) if sessions_dir.exists() else []
    session_summaries = []
    for s in recovered:
        summary_file = s / "summary.json"
        if summary_file.exists():
            session_summaries.append(json.loads(summary_file.read_text()))

    claude_sessions = [s for s in session_summaries if "20001" in s.get("session_id", "")]

    # The claude trace must be recovered (not excluded)
    assert claude_sessions, "The claude trace file must be recovered by recover_crashed_sessions"

    # The alien trace should not produce a normal session — it should be excluded
    # or marked as alien so it doesn't pollute capacity planning / anomaly analytics
    alien_included_normally = [
        s
        for s in session_summaries
        if "20002" in s.get("session_id", "") and not s.get("alien") and not s.get("pre_fix_data")
    ]
    assert not alien_included_normally, (
        "Alien trace (comm='sleep') must not be recovered as a normal session. "
        "It should be skipped or marked alien=true to prevent #771-style mis-attribution."
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: uses /proc and boot_id")
def test_recover_crashed_sessions_skips_live_pid(tmp_path):
    """A trace file whose enrolled PID is still alive must not be recovered."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    pid = os.getpid()
    trace = tmpfs / f"autoskillit_trace_{pid}.jsonl"
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    trace.write_text("")
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": read_starttime_ticks(pid),
                "session_id": "",
                "enrolled_at": datetime.now(UTC).isoformat(),
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0
    assert trace.exists(), "Trace file for alive PID must not be deleted"
    assert enrollment.exists(), "Enrollment sidecar for alive PID must not be deleted"


def test_recover_crashed_sessions_skips_file_without_enrollment(tmp_path):
    """A trace file with no enrollment sidecar must be skipped — it is not
    an autoskillit-owned trace (e.g. a test artifact or alien file)."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = tmpfs / "autoskillit_trace_99997.jsonl"
    trace.write_text("")
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0
    assert trace.exists(), "Alien trace file must not be deleted"


def test_recover_crashed_sessions_skips_wrong_boot_id(tmp_path, monkeypatch):
    """An enrollment sidecar with a different boot_id must be rejected."""
    monkeypatch.setattr(
        "autoskillit.execution.session_log.read_boot_id",
        lambda: "current-boot-id",
    )
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    pid = 99996
    trace = tmpfs / f"autoskillit_trace_{pid}.jsonl"
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    trace.write_text("")
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": "stale-boot-id",
                "starttime_ticks": 1234,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0


# --- Group H: campaign_id / dispatch_id schema tests ---


def test_flush_writes_campaign_id_to_summary(tmp_path):
    """summary.json contains campaign_id field when kwarg passed."""
    _flush(tmp_path, session_id="gh-001", campaign_id="camp-abc")
    summary = json.loads((tmp_path / "sessions" / "gh-001" / "summary.json").read_text())
    assert summary["campaign_id"] == "camp-abc"


def test_flush_writes_dispatch_id_to_summary(tmp_path):
    """summary.json contains dispatch_id field when kwarg passed."""
    _flush(tmp_path, session_id="gh-002", campaign_id="camp-abc", dispatch_id="disp-xyz")
    summary = json.loads((tmp_path / "sessions" / "gh-002" / "summary.json").read_text())
    assert summary["dispatch_id"] == "disp-xyz"


def test_flush_writes_campaign_id_to_index(tmp_path):
    """sessions.jsonl entry contains campaign_id."""
    _flush(tmp_path, session_id="gh-003", campaign_id="camp-abc")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["campaign_id"] == "camp-abc"


def test_flush_writes_dispatch_id_to_index(tmp_path):
    """sessions.jsonl entry contains dispatch_id."""
    _flush(tmp_path, session_id="gh-004", campaign_id="camp-abc", dispatch_id="disp-xyz")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["dispatch_id"] == "disp-xyz"


def test_flush_writes_meta_json_sidecar(tmp_path):
    """meta.json written with campaign_id and dispatch_id when campaign_id non-empty."""
    _flush(tmp_path, session_id="gh-005", campaign_id="c1", dispatch_id="d1")
    meta_path = tmp_path / "sessions" / "gh-005" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta == {"campaign_id": "c1", "dispatch_id": "d1"}


def test_flush_writes_meta_json_sidecar_campaign_only(tmp_path):
    """meta.json written with empty dispatch_id when only campaign_id is provided."""
    _flush(tmp_path, session_id="gh-005b", campaign_id="c1")
    meta_path = tmp_path / "sessions" / "gh-005b" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta == {"campaign_id": "c1", "dispatch_id": ""}


def test_flush_omits_meta_json_when_no_campaign(tmp_path):
    """No meta.json written when campaign_id is empty (default)."""
    _flush(tmp_path, session_id="gh-006")
    meta_path = tmp_path / "sessions" / "gh-006" / "meta.json"
    assert not meta_path.exists()


def test_flush_defaults_campaign_dispatch_empty(tmp_path):
    """Existing callers without new kwargs produce empty-string fields."""
    _flush(tmp_path, session_id="gh-007")
    summary = json.loads((tmp_path / "sessions" / "gh-007" / "summary.json").read_text())
    assert summary["campaign_id"] == ""
    assert summary["dispatch_id"] == ""
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["campaign_id"] == ""
    assert entry["dispatch_id"] == ""


# --- Group M: retention protection tests ---


def _make_sessions(tmp_path, count, start_mtime=1_000_000_000, campaign_id=""):
    """Create `count` session directories with staggered mtimes.

    Returns list of dir_names in mtime order (oldest first).
    Seeds meta.json with campaign_id if provided.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    index_path = tmp_path / "sessions.jsonl"
    dir_names = []
    for i in range(count):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir(exist_ok=True)
        mtime = start_mtime + i
        os.utime(d, (mtime, mtime))
        if campaign_id:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": campaign_id, "dispatch_id": f"d-{i}"})
            )
        with index_path.open("a") as f:
            f.write(
                json.dumps(
                    {"session_id": dir_name, "dir_name": dir_name, "campaign_id": campaign_id}
                )
                + "\n"
            )
        dir_names.append(dir_name)
    return dir_names


def _make_state_file(project_dir, campaign_id, status):
    """Create a fleet dispatch state file."""
    dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    state_path = dispatches_dir / "d1.json"
    state_path.write_text(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "dispatches": [{"name": "truck-1", "status": status}],
            }
        )
    )
    return state_path


def test_retention_protects_active_campaign_sessions(tmp_path, monkeypatch):
    """Sessions belonging to an active campaign survive retention even when expired."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create 5 "non-campaign" sessions + 2 "active campaign" sessions at the oldest positions
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    # Oldest 2 dirs: campaign sessions (will be "expired" if not protected)
    for i in range(2):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        (d / "meta.json").write_text(
            json.dumps({"campaign_id": "active-campaign", "dispatch_id": f"d{i}"})
        )
        with index_path.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "session_id": dir_name,
                        "dir_name": dir_name,
                        "campaign_id": "active-campaign",
                    }
                )
                + "\n"
            )

    # Next 4 dirs: non-campaign sessions
    for i in range(2, 6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        with index_path.open("a") as f:
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": ""})
                + "\n"
            )

    _make_state_file(project_dir, "active-campaign", "running")

    # Flush a 7th session to trigger retention (5 max, so 2 should expire)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Protected sessions survive — campaign meta.json writes update their mtime so they
    # end up in the "surviving" window; even if they landed in expired, protection saves them.
    assert (sessions_dir / "session-0000").exists(), "active campaign session must survive"
    assert (sessions_dir / "session-0001").exists(), "active campaign session must survive"
    # The 2 non-campaign sessions with oldest mtimes (0002, 0003) are deleted.
    # session-0002 and session-0003 retain the manually-set Sept-2001 mtimes (no file writes
    # update their directory mtime) so they are the oldest dirs overall.
    assert not (sessions_dir / "session-0002").exists(), (
        "oldest non-campaign session must be deleted"
    )
    assert not (sessions_dir / "session-0003").exists(), (
        "second oldest non-campaign session must be deleted"
    )
    # Newer non-campaign sessions survive (they are in the top-5 window)
    assert (sessions_dir / "session-0004").exists(), "session-0004 must survive"
    assert (sessions_dir / "session-0005").exists(), "session-0005 must survive"
    # Newly flushed session must be present
    assert (sessions_dir / "session-0006").exists()


def test_retention_deletes_released_campaign_sessions(tmp_path, monkeypatch):
    """Sessions whose campaign is in a terminal state are eligible for deletion."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    # Create 6 dirs — oldest 2 have meta.json but campaign is released
    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "done-campaign", "dispatch_id": f"d{i}"})
            )
        with index_path.open("a") as f:
            cid = "done-campaign" if i < 2 else ""
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": cid})
                + "\n"
            )
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    _make_state_file(project_dir, "done-campaign", "released")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Released campaign sessions are NOT protected — oldest 2 should be deleted
    assert not (sessions_dir / "session-0000").exists(), (
        "released campaign session must be deleted"
    )
    assert not (sessions_dir / "session-0001").exists(), (
        "released campaign session must be deleted"
    )


def test_retention_preserves_index_for_protected(tmp_path, monkeypatch):
    """Protected sessions' entries survive the sessions.jsonl rewrite."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "live-campaign", "dispatch_id": f"d{i}"})
            )
        cid = "live-campaign" if i < 2 else ""
        with index_path.open("a") as f:
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": cid})
                + "\n"
            )

    _make_state_file(project_dir, "live-campaign", "pending")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    index_lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    dir_names_in_index = {json.loads(ln)["dir_name"] for ln in index_lines}
    assert "session-0000" in dir_names_in_index, "protected session index entry must be preserved"
    assert "session-0001" in dir_names_in_index, "protected session index entry must be preserved"


def test_retention_handles_missing_meta_json(tmp_path, monkeypatch):
    """Session dirs without meta.json are not protected (normal deletion)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    _make_state_file(project_dir, "active-campaign", "running")

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        # No meta.json written — sessions are not linked to any campaign
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Oldest dirs with no meta.json are deleted normally
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_retention_handles_missing_franchise_state_dir(tmp_path, monkeypatch):
    """No franchise state files → normal retention behavior (no crash)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No dispatches dir created — build_protected_campaign_ids returns empty frozenset
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps({"campaign_id": "some-campaign", "dispatch_id": f"d{i}"})
        )
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    # Must not crash even though project_dir exists but has no dispatches dir
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Normal retention applies — oldest dirs deleted
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_retention_handles_corrupt_meta_json(tmp_path, monkeypatch):
    """Malformed meta.json → session not protected (graceful degradation)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_state_file(project_dir, "active-campaign", "running")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        if i < 2:
            # Write corrupt JSON so meta.json is unreadable
            (d / "meta.json").write_text("not valid json {{{{")
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Corrupt meta.json → not protected → deleted normally
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_session_log_removed_build_protected_function() -> None:
    """SL_CB_1: _build_protected_campaign_ids must not exist on session_log module."""
    import autoskillit.execution.session_log as sl_module

    assert not hasattr(sl_module, "_build_protected_campaign_ids")


def test_session_log_removed_terminal_statuses_constant() -> None:
    """SL_CB_2: _TERMINAL_DISPATCH_STATUSES must not exist on session_log module."""
    import autoskillit.execution.session_log as sl_module

    assert not hasattr(sl_module, "_TERMINAL_DISPATCH_STATUSES")


def test_retention_no_protection_when_callback_is_none(tmp_path: Path, monkeypatch) -> None:
    """SL_CB_6: build_protected_campaign_ids=None with active campaign → no protection applied."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_state_file(project_dir, "active-campaign", "running")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "active-campaign", "dispatch_id": f"d{i}"})
            )
            # Reset mtime after meta.json write (writing a file bumps directory mtime)
            os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        build_protected_campaign_ids=None,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # No protection applied — oldest sessions deleted even though campaign is active
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_flush_session_log_passes_callback_to_enforce_retention(
    tmp_path: Path, monkeypatch
) -> None:
    """SL_CB_7: flush_session_log forwards build_protected_campaign_ids to _enforce_retention."""
    import autoskillit.execution.session_log as sl_module

    captured: list = []

    def fake_enforce_retention(
        log_root, project_dir="", build_protected_campaign_ids=None
    ) -> None:
        captured.append(build_protected_campaign_ids)

    monkeypatch.setattr(sl_module, "_enforce_retention", fake_enforce_retention)

    sentinel = build_protected_campaign_ids
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(tmp_path),
        build_protected_campaign_ids=sentinel,
        session_id="session-cb7",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    assert (tmp_path / "sessions.jsonl").exists()
    assert len(captured) == 1
    assert captured[0] is sentinel

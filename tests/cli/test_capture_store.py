"""Tests for the ``autoskillit capture-store`` CLI command and its doctor check.

Both surfaces are backed by the single read-only adapter
``hooks._capture._reconcile.capture_store_stats`` — the doctor battery's
architectural read-only guard (``tests/arch/test_doctor_readonly.py``) only
walks ``run_doctor()``'s own body, not callees, so the tests here prove
``_check_capture_store_stats`` is genuinely read-only by construction, not
merely by that guard's limited reach.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.cli._capture_store as capture_store_command
import autoskillit.cli.doctor._doctor_capture_store as doctor_capture_store
import autoskillit.hooks._capture._reconcile as capture_reconcile
from autoskillit.cli._capture_store import run_capture_store
from autoskillit.cli.doctor._doctor_capture_store import _check_capture_store_stats
from autoskillit.core import Severity
from autoskillit.hooks._capture._authority import open_capture_root, open_project_anchor
from autoskillit.hooks._capture._orphan_scan import ADOPTION_AGE_SECONDS
from autoskillit.hooks._capture._reconcile import CaptureStoreStats, capture_store_stats
from autoskillit.hooks._capture._types import CleanupBlocker
from autoskillit.hooks._capture_lifecycle import LOCK_NAME, CaptureLifecycleStore

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _seed_store_with_backlog_and_orphans(project: Path) -> Path:
    """Seed both a ledger backlog and aged unledgered orphans in one project.

    ``run_capture_store``/``_check_capture_store_stats`` always open the
    store with the real wall clock (no clock injection at the CLI layer),
    so the ledger records are seeded through a store whose *own* wall clock
    already reads well in the past — the resulting ``next_attempt_at`` is a
    plain float already due relative to real "now" once written, regardless
    of which clock computed it.
    """
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    try:
        past_clock = lambda: time.time() - 4000.0  # noqa: E731
        store = CaptureLifecycleStore.from_open_authorities(anchor, root, wall_clock=past_clock)
        for index in range(5):
            store.reserve_capture(f"{index + 1:016x}")

        old = time.time() - ADOPTION_AGE_SECONDS - 10
        for index in range(10):
            name = f"shell_{index + 0x9000:016x}.log"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root.fd)
            os.write(fd, b"orphan-debris")
            os.close(fd)
            os.utime(name, (old, old), dir_fd=root.fd)
    finally:
        root.close()
        anchor.close()
    return project / ".autoskillit" / "temp" / "shell_capture"


def _snapshot(capture_dir: Path) -> dict[str, tuple[int, float]]:
    return {
        entry.name: (entry.stat().st_size, entry.stat().st_mtime)
        for entry in capture_dir.iterdir()
    }


def test_doctor_capture_store_check_reports_stats_without_mutating(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    capture_dir = _seed_store_with_backlog_and_orphans(project)
    before = _snapshot(capture_dir)

    result = _check_capture_store_stats(project)

    after = _snapshot(capture_dir)
    assert after == before
    assert "live=5" in result.message
    assert "unledgered_aged=10" in result.message

    stats = capture_store_stats(str(project))
    assert stats.blocker is CleanupBlocker.NONE
    assert stats.live_records == 5
    assert stats.ledger_bytes > 0
    assert stats.unledgered_aged_files == 10
    assert stats.unledgered_aged_bytes == 10 * len(b"orphan-debris")


def test_doctor_capture_store_reports_absent_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def absent(project: str) -> CaptureStoreStats:
        calls.append(project)
        return CaptureStoreStats(CleanupBlocker.STORE_ABSENT)

    monkeypatch.setattr(doctor_capture_store, "capture_store_stats", absent)

    result = doctor_capture_store._check_capture_store_stats(tmp_path)

    assert calls == [str(tmp_path)]
    assert result.severity is Severity.OK
    assert result.check == "capture_store_stats"
    assert result.message == "No capture store yet"


@pytest.mark.parametrize(
    "blocker",
    [
        blocker
        for blocker in CleanupBlocker
        if blocker not in {CleanupBlocker.NONE, CleanupBlocker.STORE_ABSENT}
    ],
)
def test_doctor_capture_store_maps_unavailable_blockers_to_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocker: CleanupBlocker,
) -> None:
    monkeypatch.setattr(
        doctor_capture_store,
        "capture_store_stats",
        lambda _project: CaptureStoreStats(blocker),
    )

    result = doctor_capture_store._check_capture_store_stats(tmp_path)

    assert result.severity is Severity.WARNING
    assert result.check == "capture_store_stats"
    assert f"blocker={blocker.value}" in result.message


@pytest.mark.parametrize(
    ("unledgered", "expected_severity", "expects_remediation"),
    [(99, Severity.OK, False), (100, Severity.WARNING, True)],
)
def test_doctor_capture_store_warning_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unledgered: int,
    expected_severity: Severity,
    expects_remediation: bool,
) -> None:
    monkeypatch.setattr(
        doctor_capture_store,
        "capture_store_stats",
        lambda _project: CaptureStoreStats(
            blocker=CleanupBlocker.NONE,
            live_records=3,
            eligible_records=2,
            deleting_records=1,
            ledger_bytes=123,
            directory_files=7,
            unledgered_aged_files=unledgered,
            unledgered_aged_bytes=456,
        ),
    )

    result = doctor_capture_store._check_capture_store_stats(tmp_path)

    assert result.severity is expected_severity
    assert result.check == "capture_store_stats"
    assert "live=3" in result.message
    assert "eligible=2" in result.message
    assert "deleting=1" in result.message
    assert ("--reclaim" in result.message) is expects_remediation


def test_capture_store_command_without_reclaim_does_not_mutate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    capture_dir = _seed_store_with_backlog_and_orphans(project)
    before = _snapshot(capture_dir)
    monkeypatch.chdir(project)

    run_capture_store(reclaim=False)

    after = _snapshot(capture_dir)
    assert after == before
    out = capsys.readouterr().out
    assert "live=5" in out
    assert "unledgered_aged=10" in out


def test_capture_store_reclaim_drains_ledger_and_orphan_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    capture_dir = _seed_store_with_backlog_and_orphans(project)
    monkeypatch.chdir(project)

    run_capture_store(reclaim=True)

    out = capsys.readouterr().out
    assert "converged" in out
    remaining_shell_files = sorted(p.name for p in capture_dir.glob("shell_*.log"))
    assert remaining_shell_files == []

    final_check = _check_capture_store_stats(project)
    assert "live=0" in final_check.message
    assert "unledgered_aged=0" in final_check.message


def test_capture_store_reclaim_waits_for_complete_orphan_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    capture_dir = _seed_store_with_backlog_and_orphans(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        capture_store_command,
        "RECLAIM_BUDGET",
        replace(capture_store_command.RECLAIM_BUDGET, max_directory_entries_scanned=1),
    )

    capture_store_command.run_capture_store(reclaim=True)

    assert "converged" in capsys.readouterr().out
    assert list(capture_dir.glob("shell_*.log")) == []


def test_capture_store_reclaim_retries_capacity_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    capture_dir = _seed_store_with_backlog_and_orphans(project)
    monkeypatch.chdir(project)
    real_admit = CaptureLifecycleStore._admit_new_record
    refused_once = False

    def refuse_first_orphan(self, record, records, compaction_epoch, size):
        nonlocal refused_once
        if not refused_once and record.legacy_cleanup is not None:
            refused_once = True
            return False
        return real_admit(self, record, records, compaction_epoch, size)

    monkeypatch.setattr(CaptureLifecycleStore, "_admit_new_record", refuse_first_orphan)

    capture_store_command.run_capture_store(reclaim=True)

    assert refused_once
    assert "converged" in capsys.readouterr().out
    assert list(capture_dir.glob("shell_*.log")) == []


def test_capture_store_stats_does_not_hang_on_lock_contention(tmp_path: Path) -> None:
    """A diagnostic stats read must never hang: capture_store_stats() opens
    with RUNNER_TAIL_BUDGET as its open_budget, so a contended store-open
    lock is bounded the same way a real sweep would be — the doctor check
    and the CLI's default (no --reclaim) path both depend on this to stay
    responsive under contention rather than blocking indefinitely."""
    project = tmp_path / "project"
    project.mkdir()
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    store = CaptureLifecycleStore.from_open_authorities(anchor, root)
    store.reserve_capture("1" * 16)
    root.close()
    anchor.close()

    lock_path = project / ".autoskillit" / "temp" / "shell_capture" / LOCK_NAME
    holder_script = (
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('ready', flush=True)\n"
        "time.sleep(1.0)\n"
        "os.close(fd)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline() == "ready\n"

        started = time.monotonic()
        stats = capture_store_stats(str(project))
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, f"capture_store_stats hung under lock contention: {elapsed}s"
        assert stats.blocker is CleanupBlocker.LOCK_CONTENDED
    finally:
        try:
            holder.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder.communicate(timeout=3)


def test_capture_store_stats_surfaces_entry_authority_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _seed_store_with_backlog_and_orphans(project)

    class DeniedEntry:
        name = "shell_0000000000000001.log"

        @staticmethod
        def stat(*, follow_symlinks: bool):
            raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(capture_reconcile.os, "scandir", lambda _fd: [DeniedEntry()])

    stats = capture_reconcile.capture_store_stats(str(project))

    assert stats.blocker is CleanupBlocker.PERMISSION_DENIED

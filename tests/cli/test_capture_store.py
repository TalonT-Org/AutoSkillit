"""Tests for the ``autoskillit capture-store`` CLI command and its doctor check.

Both surfaces are backed by the single read-only adapter
``hooks._capture._reconcile.capture_store_stats`` — the doctor battery's
architectural read-only guard (``tests/arch/test_doctor_readonly.py``) only
walks ``run_doctor()``'s own body, not callees, so the tests here prove
``_check_capture_store_stats`` is genuinely read-only by construction, not
merely by that guard's limited reach.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from autoskillit.cli._capture_store import run_capture_store
from autoskillit.cli.doctor._doctor_capture_store import _check_capture_store_stats
from autoskillit.hooks._capture._authority import open_capture_root, open_project_anchor
from autoskillit.hooks._capture._orphan_scan import ADOPTION_AGE_SECONDS
from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore

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

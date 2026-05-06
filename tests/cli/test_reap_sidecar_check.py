"""Tests for _reap_stale_dispatches sidecar-aware status transition (T-RESUMABLE-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    mark_dispatch_running,
    read_state,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    p = tmp_path / "campaign" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_reap_stale_dispatches_marks_resumable_when_sidecar_exists(tmp_path: Path) -> None:
    from autoskillit.cli.fleet import _reap_stale_dispatches

    sp = _state_path(tmp_path)
    write_initial_state(sp, "c1", "camp", "manifest.yaml", [DispatchRecord(name="impl")])
    sidecar_file = sp.parent / "d1111_issues.jsonl"
    mark_dispatch_running(
        sp, "impl", dispatch_id="d1111", dispatched_pid=0, sidecar_path=str(sidecar_file)
    )
    sidecar_file.write_text(
        '{"issue_url":"https://github.com/o/r/issues/1","status":"completed","ts":"2026-01-01T00:00:00"}\n'
    )

    _reap_stale_dispatches(sp)

    state = read_state(sp)
    assert state is not None
    matches = [d for d in state.dispatches if d.name == "impl"]
    assert matches, "no dispatch named 'impl' found"
    assert matches[-1].status == DispatchStatus.RESUMABLE


def test_reap_stale_dispatches_marks_interrupted_when_no_sidecar(tmp_path: Path) -> None:
    from autoskillit.cli.fleet import _reap_stale_dispatches

    sp = _state_path(tmp_path)
    write_initial_state(sp, "c1", "camp", "manifest.yaml", [DispatchRecord(name="impl")])
    mark_dispatch_running(sp, "impl", dispatch_id="d1111", dispatched_pid=0)

    _reap_stale_dispatches(sp)

    state = read_state(sp)
    assert state is not None
    matches = [d for d in state.dispatches if d.name == "impl"]
    assert matches, "no dispatch named 'impl' found"
    assert matches[-1].status == DispatchStatus.INTERRUPTED


def test_reap_stale_dispatches_dry_run_does_not_modify_state(tmp_path: Path) -> None:
    from autoskillit.cli.fleet import _reap_stale_dispatches

    sp = _state_path(tmp_path)
    write_initial_state(sp, "c1", "camp", "manifest.yaml", [DispatchRecord(name="impl")])
    sidecar_file = sp.parent / "d1111_issues.jsonl"
    mark_dispatch_running(
        sp, "impl", dispatch_id="d1111", dispatched_pid=0, sidecar_path=str(sidecar_file)
    )
    sidecar_file.write_text(
        '{"issue_url":"https://github.com/o/r/issues/1","status":"completed","ts":"2026-01-01T00:00:00"}\n'
    )

    _reap_stale_dispatches(sp, dry_run=True)

    state = read_state(sp)
    assert state is not None
    matches = [d for d in state.dispatches if d.name == "impl"]
    assert matches, "no dispatch named 'impl' found"
    assert matches[-1].status == DispatchStatus.RUNNING

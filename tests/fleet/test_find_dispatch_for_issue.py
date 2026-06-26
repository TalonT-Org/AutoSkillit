"""Tests for find_dispatch_for_issue in fleet/state_recovery.py."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    IssueSidecarEntry,
    find_dispatch_for_issue,
    write_initial_state,
)

pytestmark = [pytest.mark.feature("fleet"), pytest.mark.layer("fleet"), pytest.mark.small]

_ISSUE_URL = "https://github.com/owner/repo/issues/42"
_OTHER_URL = "https://github.com/owner/repo/issues/99"
_TS = "2026-01-01T00:00:00Z"


def _write_state(state_path: Path, dispatches: list[DispatchRecord]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_initial_state(state_path, "cid", "test-campaign", "/m.yaml", dispatches)


def _write_sidecar(sidecar_path: Path, entries: list[IssueSidecarEntry]) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open("w") as f:
        for entry in entries:
            payload = {k: v for k, v in asdict(entry).items() if v is not None}
            f.write(json.dumps(payload) + "\n")


def test_finds_running_dispatch_with_matching_issue_url(tmp_path):
    sidecar = tmp_path / "s1.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-1", status=DispatchStatus.RUNNING, sidecar_path=str(sidecar)
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-1"


def test_returns_none_when_no_matching_issue_url(tmp_path):
    sidecar = tmp_path / "s2.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_OTHER_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-2", status=DispatchStatus.RUNNING, sidecar_path=str(sidecar)
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is None


def test_returns_none_when_dispatch_not_running(tmp_path):
    sidecar = tmp_path / "s3.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-3", status=DispatchStatus.SUCCESS, sidecar_path=str(sidecar)
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is None


def test_returns_none_when_no_campaign_state_files(tmp_path):
    result = find_dispatch_for_issue(_ISSUE_URL, [])
    assert result is None


def test_handles_missing_sidecar_gracefully(tmp_path):
    dispatch = DispatchRecord(
        name="task-4",
        status=DispatchStatus.RUNNING,
        sidecar_path=str(tmp_path / "nonexistent.jsonl"),
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is None


def test_handles_none_sidecar_path_gracefully(tmp_path):
    dispatch = DispatchRecord(name="task-5", status=DispatchStatus.RUNNING, sidecar_path=None)
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is None


def test_finds_running_dispatch_via_issue_url_when_sidecar_missing(tmp_path):
    dispatch = DispatchRecord(
        name="task-url-1",
        status=DispatchStatus.RUNNING,
        sidecar_path=str(tmp_path / "nonexistent.jsonl"),
        issue_url=_ISSUE_URL,
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-url-1"


def test_finds_running_dispatch_via_issue_url_when_sidecar_none(tmp_path):
    dispatch = DispatchRecord(
        name="task-url-2",
        status=DispatchStatus.RUNNING,
        sidecar_path=None,
        issue_url=_ISSUE_URL,
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-url-2"


def test_finds_failure_dispatch_via_issue_url_when_sidecar_none(tmp_path):
    dispatch = DispatchRecord(
        name="task-url-3",
        status=DispatchStatus.FAILURE,
        sidecar_path=None,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-url-3"


def test_skips_corrupt_state_file_and_continues(tmp_path):
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("not valid json {{{{")

    sidecar = tmp_path / "s6.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-6", status=DispatchStatus.RUNNING, sidecar_path=str(sidecar)
    )
    valid_state_path = tmp_path / "state.json"
    _write_state(valid_state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [corrupt_path, valid_state_path])

    assert result is not None
    assert result.name == "task-6"


def test_finds_failure_dispatch_with_uncleaned_labels(tmp_path):
    sidecar = tmp_path / "s7.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-7",
        status=DispatchStatus.FAILURE,
        sidecar_path=str(sidecar),
        labels_cleaned=False,
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-7"


def test_returns_none_for_failure_dispatch_with_cleaned_labels(tmp_path):
    sidecar = tmp_path / "s8.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    dispatch = DispatchRecord(
        name="task-8",
        status=DispatchStatus.FAILURE,
        sidecar_path=str(sidecar),
        labels_cleaned=True,
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, [dispatch])

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is None


def test_running_dispatch_takes_priority_over_failure(tmp_path):
    sidecar_running = tmp_path / "s9_run.jsonl"
    _write_sidecar(
        sidecar_running, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)]
    )
    sidecar_failure = tmp_path / "s9_fail.jsonl"
    _write_sidecar(
        sidecar_failure, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)]
    )
    dispatches = [
        DispatchRecord(
            name="task-fail",
            status=DispatchStatus.FAILURE,
            sidecar_path=str(sidecar_failure),
            labels_cleaned=False,
        ),
        DispatchRecord(
            name="task-run",
            status=DispatchStatus.RUNNING,
            sidecar_path=str(sidecar_running),
        ),
    ]
    state_path = tmp_path / "state.json"
    _write_state(state_path, dispatches)

    result = find_dispatch_for_issue(_ISSUE_URL, [state_path])

    assert result is not None
    assert result.name == "task-run"


# --- PENDING dispatch search (Pass 3) ---


def test_pending_dispatch_stale_match(tmp_path):
    """PENDING dispatch with stale attempt_history is returned."""
    sp = tmp_path / "campaign.json"
    d = DispatchRecord(
        name="d1",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[{"ended_at": time.time() - 120, "status": "failure"}],
    )
    _write_state(sp, [d])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is not None
    assert result.name == "d1"


def test_pending_dispatch_too_fresh_skipped(tmp_path):
    """PENDING dispatch within quiet period is skipped."""
    sp = tmp_path / "campaign.json"
    d = DispatchRecord(
        name="d1",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[{"ended_at": time.time() - 5, "status": "failure"}],
    )
    _write_state(sp, [d])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is None


def test_pending_dispatch_no_history_skipped(tmp_path):
    """PENDING dispatch with empty attempt_history is skipped (never dispatched)."""
    sp = tmp_path / "campaign.json"
    d = DispatchRecord(
        name="d1",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[],
    )
    _write_state(sp, [d])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is None


def test_pending_dispatch_with_session_id_skipped(tmp_path):
    """PENDING dispatch with active dispatched_session_id is skipped."""
    sp = tmp_path / "campaign.json"
    d = DispatchRecord(
        name="d1",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="abc",
        attempt_history=[{"ended_at": time.time() - 120, "status": "failure"}],
    )
    _write_state(sp, [d])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is None


def test_running_beats_pending(tmp_path):
    """RUNNING dispatch wins over stale PENDING dispatch."""
    sidecar = tmp_path / "sidecar.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="completed", ts=_TS)])
    running = DispatchRecord(
        name="task-run",
        status=DispatchStatus.RUNNING,
        sidecar_path=str(sidecar),
    )
    pending = DispatchRecord(
        name="task-pending",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[{"ended_at": time.time() - 120, "status": "failure"}],
    )
    sp = tmp_path / "campaign.json"
    _write_state(sp, [pending, running])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is not None
    assert result.name == "task-run"


def test_terminal_beats_pending(tmp_path):
    """Terminal FAILURE wins over stale PENDING dispatch."""
    sidecar = tmp_path / "sidecar.jsonl"
    _write_sidecar(sidecar, [IssueSidecarEntry(issue_url=_ISSUE_URL, status="failed", ts=_TS)])
    failure = DispatchRecord(
        name="task-fail",
        status=DispatchStatus.FAILURE,
        sidecar_path=str(sidecar),
        labels_cleaned=False,
    )
    pending = DispatchRecord(
        name="task-pending",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[{"ended_at": time.time() - 120, "status": "failure"}],
    )
    sp = tmp_path / "campaign.json"
    _write_state(sp, [pending, failure])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is not None
    assert result.name == "task-fail"


def test_pending_dispatch_ended_at_zero_treated_as_stale(tmp_path):
    """PENDING dispatch with ended_at=0.0 (process crashed unrecorded) is treated as stale."""
    sp = tmp_path / "campaign.json"
    d = DispatchRecord(
        name="d1",
        status=DispatchStatus.PENDING,
        issue_url=_ISSUE_URL,
        labels_cleaned=False,
        dispatched_session_id="",
        attempt_history=[{"ended_at": 0.0, "status": "interrupted"}],
    )
    _write_state(sp, [d])

    result = find_dispatch_for_issue(_ISSUE_URL, [sp])

    assert result is not None
    assert result.name == "d1"

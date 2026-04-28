"""Tests for _compute_exit_code in cli/_fleet.py (T-RESUMABLE-9)."""

from __future__ import annotations

import time

import pytest

from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_state(*statuses: DispatchStatus) -> CampaignState:
    return CampaignState(
        schema_version=3,
        campaign_id="test-cid",
        campaign_name="test",
        manifest_path="",
        started_at=time.time(),
        dispatches=[DispatchRecord(name=f"d{i}", status=s) for i, s in enumerate(statuses)],
    )


def test_compute_exit_code_all_success_returns_0() -> None:
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(DispatchStatus.SUCCESS, DispatchStatus.SKIPPED)
    assert _compute_exit_code(state) == 0


def test_compute_exit_code_resumable_returns_2() -> None:
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(DispatchStatus.SUCCESS, DispatchStatus.RESUMABLE)
    assert _compute_exit_code(state) == 2


def test_compute_exit_code_failure_beats_resumable() -> None:
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(DispatchStatus.FAILURE, DispatchStatus.RESUMABLE)
    assert _compute_exit_code(state) == 1


def test_compute_exit_code_interrupted_returns_1() -> None:
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(DispatchStatus.SUCCESS, DispatchStatus.INTERRUPTED)
    assert _compute_exit_code(state) == 1


def test_compute_exit_code_pending_returns_2() -> None:
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(DispatchStatus.SUCCESS, DispatchStatus.PENDING)
    assert _compute_exit_code(state) == 2

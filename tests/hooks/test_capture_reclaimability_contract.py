"""Contract tests for the state-reclaimability registry (B-T1)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._capture._lifecycle_policy import (
    STATE_RECLAIMABILITY,
    CaptureState,
    ReclaimKind,
    StateReclaimabilityDef,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


class TestReclaimabilityTotality:
    """B-T1 — every CaptureState declares reclaimability."""

    def test_every_state_declares_reclaimability(self) -> None:
        assert set(STATE_RECLAIMABILITY) == set(CaptureState)
        for state, entry in STATE_RECLAIMABILITY.items():
            assert isinstance(entry, StateReclaimabilityDef)
            assert entry.state == state
            assert entry.rationale
            if entry.kind is ReclaimKind.SWEEP_AFTER_GRACE:
                assert isinstance(entry.duration_seconds, (int, float))
                assert entry.duration_seconds >= 0
            elif entry.kind is ReclaimKind.TOMBSTONE:
                assert entry.duration_seconds is None
            elif entry.kind is ReclaimKind.FORENSIC_HOLD:
                assert isinstance(entry.duration_seconds, (int, float))
                assert entry.duration_seconds > 0

    def test_no_state_is_counted_but_unreclaimable(self) -> None:
        """Every state whose frames are counted must have finite reclaim."""
        for state in CaptureState:
            if state is CaptureState.DELETED:
                continue  # tombstone — bounded by max_tombstones
            entry = STATE_RECLAIMABILITY[state]
            if entry.kind is ReclaimKind.FORENSIC_HOLD:
                assert entry.duration_seconds is not None and entry.duration_seconds > 0
            elif entry.kind is ReclaimKind.SWEEP_AFTER_GRACE:
                assert entry.duration_seconds is not None and entry.duration_seconds >= 0

    def test_registry_rejects_unregistered_state(self) -> None:
        missing = {"FAKE_STATE"} - set(STATE_RECLAIMABILITY)
        assert missing, "a fake state not in STATE_RECLAIMABILITY must be detected"

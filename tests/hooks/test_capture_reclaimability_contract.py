"""Contract tests for the state-reclaimability registry (B-T1)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._capture import _ledger, _ledger_view
from autoskillit.hooks._capture._lifecycle_policy import (
    _RETENTION_SUCCESSORS,
    _STATE_SUCCESSORS,
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


def test_opaque_future_frame_is_removed_before_lifecycle_policy() -> None:
    record = _ledger.CaptureLifecycleRecord(
        capture_id="0123456789abcdef",
        state=CaptureState.RESERVED,
        staging_name=".capture-staging-0123456789abcdef-0000000000000000",
        public_name="shell_0123456789abcdef.log",
        project_identity=(1, 2),
        root_identity=(3, 4),
        created_at=1.0,
        next_attempt_at=2.0,
        incarnation="1" * 32,
        revision=1,
    )
    persisted = _ledger.record_to_dict(record)
    persisted["state"] = "future-state"
    opaque = _ledger.encode_frame(persisted, compaction_epoch=1)

    decoded = _ledger_view._decode_full(opaque)

    assert decoded.records == {}
    assert decoded.opaque_frames == (opaque,)
    assert "future-state" not in STATE_RECLAIMABILITY
    assert "future-state" not in _STATE_SUCCESSORS
    assert "future-state" not in _RETENTION_SUCCESSORS

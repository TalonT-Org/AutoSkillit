"""Contract tests for the failure-disposition registry (A-T1)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._capture._failure_policy import (
    FAILURE_DISPOSITIONS,
    CaptureFailureDisposition,
    CaptureFailureDispositionDef,
    CaptureFailureReason,
)
from autoskillit.hooks._capture._snapshot import CaptureAuthorityError
from autoskillit.hooks._capture._types import CaptureFailureEvidence

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


class TestDispositionRegistryTotality:
    """A-T1 — every CaptureFailureReason has a disposition."""

    def test_every_failure_reason_has_a_disposition(self) -> None:
        assert set(FAILURE_DISPOSITIONS) == set(CaptureFailureReason)
        for reason, entry in FAILURE_DISPOSITIONS.items():
            assert isinstance(entry, CaptureFailureDispositionDef)
            assert isinstance(entry.disposition, CaptureFailureDisposition)
            assert entry.reason == reason, f"key {reason!r} != entry.reason {entry.reason!r}"
            assert entry.rationale, f"empty rationale for {reason!r}"

    def test_transition_reachable_bookkeeping_reasons_preserve_output(self) -> None:
        for reason in (
            CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED,
            CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED,
            CaptureFailureReason.LEDGER_INTEGRITY,
        ):
            assert (
                FAILURE_DISPOSITIONS[reason].disposition
                is CaptureFailureDisposition.PRESERVE_OUTPUT
            ), f"{reason!r} should preserve output"
        assert (
            FAILURE_DISPOSITIONS[CaptureFailureReason.UNKNOWN_SETUP].disposition
            is CaptureFailureDisposition.DISCARD_OUTPUT
        )


def test_failure_evidence_rejects_unknown_reason_wire_value() -> None:
    with pytest.raises(CaptureAuthorityError, match="invalid capture failure evidence"):
        CaptureFailureEvidence(
            stage="capture_failure",
            detail="failure detail",
            failure_reason="NOT_A_CAPTURE_FAILURE_REASON",
        )

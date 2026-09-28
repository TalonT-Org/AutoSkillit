"""Contract tests for the failure-disposition registry (A-T1)."""

from __future__ import annotations

import re
from pathlib import Path

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
            CaptureFailureReason.RECLAMATION_DEBT_ASSIST,
            CaptureFailureReason.RECLAMATION_DEBT_STALL,
            CaptureFailureReason.LEDGER_INTEGRITY,
        ):
            assert (
                FAILURE_DISPOSITIONS[reason].disposition
                is CaptureFailureDisposition.PRESERVE_OUTPUT
            ), f"{reason!r} should preserve output"
        # ADR-0009 Accepted Gap 1: UNKNOWN_SETUP remains the verify-stage
        # tamper wire label and must fail closed until it gets a dedicated reason.
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


def test_runner_settlement_discards_output() -> None:
    assert (
        FAILURE_DISPOSITIONS[CaptureFailureReason.RUNNER_SETTLEMENT].disposition
        is CaptureFailureDisposition.DISCARD_OUTPUT
    )


def test_adr_0009_table_lists_every_reason() -> None:
    runner_settlement = CaptureFailureReason.RUNNER_SETTLEMENT
    adr = (
        Path(__file__).resolve().parents[2]
        / "docs/decisions/0009-verified-output-delivery-disposition.md"
    )
    lines = adr.read_text(encoding="utf-8").splitlines()
    header = "| Disposition | Reasons | Rationale |"
    table_start = lines.index(header) + 1

    listed: dict[CaptureFailureReason, CaptureFailureDisposition] = {}
    listed_names: list[str] = []
    for line in lines[table_start:]:
        if not line.startswith("|"):
            break
        if line.startswith("|---"):
            continue
        disposition_name, reasons_cell, _rationale = (
            cell.strip() for cell in line.strip("|").split("|", maxsplit=2)
        )
        disposition = CaptureFailureDisposition[disposition_name.strip("`")]
        for reason_name in re.findall(r"`([A-Z][A-Z0-9_]*)`", reasons_cell):
            reason = CaptureFailureReason[reason_name]
            listed_names.append(reason_name)
            listed[reason] = disposition

    assert runner_settlement in listed
    assert len(listed_names) == len(set(listed_names)), "ADR-0009 lists a reason more than once"
    assert set(listed) == set(CaptureFailureReason)
    for reason, disposition in listed.items():
        assert FAILURE_DISPOSITIONS[reason].disposition is disposition

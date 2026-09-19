"""Waiver-ledger loading and admission checks for audit findings."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

from ..io import YAMLError, load_yaml
from ..io.path_containment import ContainmentError
from ..types._type_audit_cycle_authority import (
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditDisposition,
)
from ..types._type_audit_cycle_disposition import (
    AdmissionReason,
    AuditFindingWaiver,
    InventoryAdmissionDecision,
    PlanDispositionRow,
)


class WaiverLedgerReader(Protocol):
    def __call__(
        self, path: str | Path, allowed_root: str | Path, *, max_size_bytes: int
    ) -> tuple[Path, bytes]: ...


def load_audit_finding_waivers(
    *,
    waiver_root: Path | None,
    reader: WaiverLedgerReader,
    max_size_bytes: int,
) -> tuple[AuditFindingWaiver, ...]:
    """Load the human-maintained waiver ledger from its explicit root."""
    if waiver_root is None:
        return ()
    ledger_path = waiver_root / ".autoskillit/waivers/audit-findings.yaml"
    try:
        _, data = reader(ledger_path, waiver_root, max_size_bytes=max_size_bytes)
    except FileNotFoundError:
        return ()
    except (ContainmentError, OSError) as exc:
        raise ValueError(f"waiver ledger containment/read failed: {exc}") from exc
    try:
        raw = load_yaml(data.decode("utf-8", errors="strict"))
        if not isinstance(raw, dict) or set(raw) != {"waivers"}:
            raise ValueError("waiver ledger must be a mapping with only waivers")
        entries = raw["waivers"]
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("waiver ledger waivers must be a list of mappings")
        waivers = tuple(AuditFindingWaiver.from_dict(entry) for entry in entries)
        if len({waiver.waiver_id for waiver in waivers}) != len(waivers):
            raise ValueError("waiver ledger contains duplicate waiver IDs")
        return waivers
    except (UnicodeDecodeError, YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"waiver ledger is invalid: {exc}") from exc


def waiver_rejection(
    *,
    assessment: AuditAssessmentRow,
    authority: AuditCycleAuthority,
    waiver_id: str | None,
    waiver_by_id: dict[str, AuditFindingWaiver],
    as_of: date | None = None,
) -> InventoryAdmissionDecision | None:
    """Return the rejection for a decision waiver, if the row needs one."""
    if (
        waiver_id is not None
        and assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION
    ):
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_NOT_APPLICABLE,
            f"{assessment.requirement_id} cannot be waived by decision",
        )
    if assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION:
        return None
    if waiver_id is None:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.UNMAPPED_REQUIREMENT,
            f"{assessment.requirement_id} requires waived-by-decision@<id>",
        )
    waiver = waiver_by_id.get(waiver_id)
    if waiver is None:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_NOT_FOUND,
            f"{assessment.requirement_id} names unknown waiver {waiver_id!r}",
        )
    if (
        waiver.requirement_id != assessment.requirement_id
        or waiver.finding_row_digest != assessment.row_digest
    ):
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_DIGEST_MISMATCH,
            f"{assessment.requirement_id} does not match waiver {waiver_id!r}",
        )
    if waiver.plan_set_id != authority.plan_set_id or waiver.scope_id != authority.scope_id:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_SCOPE_MISMATCH,
            f"{assessment.requirement_id} waiver scope does not match authority",
        )
    if waiver.part_id != authority.part_id:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_PART_MISMATCH,
            f"{assessment.requirement_id} waiver part does not match authority",
        )
    try:
        is_stale = waiver.is_stale(as_of=as_of or date.today())
    except ValueError as exc:
        return InventoryAdmissionDecision.reject(AdmissionReason.WAIVER_LEDGER_INVALID, str(exc))
    if is_stale:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_EXPIRED,
            f"{assessment.requirement_id} waiver {waiver_id!r} has expired",
        )
    return None


def validate_waiver_rows(
    *,
    authority: AuditCycleAuthority,
    plan_rows: tuple[PlanDispositionRow, ...],
    waivers: tuple[AuditFindingWaiver, ...],
    as_of: date | None = None,
) -> tuple[
    InventoryAdmissionDecision | None,
    tuple[tuple[AuditAssessmentRow, PlanDispositionRow], ...],
]:
    """Validate decision waivers and return the rows for legacy admission checks."""
    if not all(isinstance(waiver, AuditFindingWaiver) for waiver in waivers):
        return (
            InventoryAdmissionDecision.reject(
                AdmissionReason.WAIVER_LEDGER_INVALID,
                "waiver ledger contains invalid records",
            ),
            (),
        )
    waiver_by_id = {waiver.waiver_id: waiver for waiver in waivers}
    if len(waiver_by_id) != len(waivers):
        return (
            InventoryAdmissionDecision.reject(
                AdmissionReason.WAIVER_LEDGER_INVALID,
                "waiver ledger contains duplicate waiver IDs",
            ),
            (),
        )
    legacy_rows: list[tuple[AuditAssessmentRow, PlanDispositionRow]] = []
    for assessment, disposition in zip(authority.assessments, plan_rows, strict=True):
        rejection = waiver_rejection(
            assessment=assessment,
            authority=authority,
            waiver_id=disposition.waiver_id,
            waiver_by_id=waiver_by_id,
            as_of=as_of,
        )
        if rejection is not None:
            return rejection, ()
        if assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION:
            legacy_rows.append((assessment, disposition))
    return None, tuple(legacy_rows)

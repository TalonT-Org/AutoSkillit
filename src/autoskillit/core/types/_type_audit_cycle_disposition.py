"""Immutable audit-cycle plan-disposition value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Self, assert_never

from ..audit.closure_hashing import canonical_json_bytes, compute_canonical_hash
from ._type_audit_admission_validation import (
    _require_digest,
    _require_nonempty,
    _require_positive_int,
)
from ._type_audit_artifact_ref import ArtifactRef
from ._type_audit_cycle_authority import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    _immutable_typed_tuple,
)

__all__ = [
    "AdmissionReason",
    "AdmissionStatus",
    "AuditFindingWaiver",
    "InventoryAdmissionDecision",
    "PlanDispositionReport",
    "PlanDispositionRow",
    "_MAX_ASSOCIATION_FILES",
    "_PLAN_ASSOCIATION_DOMAIN",
    "_PLAN_ASSOCIATION_KEYS",
]

_DISPOSITION_ROW_DOMAIN = "autoskillit:audit-cycle:disposition-row:v1:sha256"
_REPORT_DOMAIN = "autoskillit:audit-cycle:plan-disposition:v1:sha256"
_SATISFIED_RE = re.compile(r"^satisfied-by-round-([1-9][0-9]*)$")
_WAIVED_RE = re.compile(r"^waived-by-decision@([A-Za-z0-9][A-Za-z0-9._-]{0,63})$")
_MIN_RATIONALE_LENGTH = 20

# Shared by the private verified-copy disposition producer and the read-side
# _resolve_plan_disposition (recipe/_cmd_rpc_guards.py) so the two sides of the
# plan-association contract cannot drift into DUAL-COPY CONSTANTS.
_PLAN_ASSOCIATION_DOMAIN = "autoskillit:audit-cycle:plan-association:v1:sha256"
_PLAN_ASSOCIATION_KEYS = frozenset(
    {
        "schema_version",
        "plan_ref",
        "disposition_ref",
        "parent_authority_digest",
        "association_digest",
    }
)
_MAX_ASSOCIATION_FILES = 256


class AdmissionStatus(StrEnum):
    OMIT = "OMIT"
    PASS = "PASS"
    REJECT = "REJECT"


class AdmissionReason(StrEnum):
    NO_AUTHORITY = "no_authority"
    TRUSTED_GO = "trusted_go"
    TRUSTED_GO_SUCCESSOR = "trusted_go_successor"
    ADMITTED = "admitted"
    REPORT_WITHOUT_AUTHORITY = "report_without_authority"
    HEAD_MISSING = "head_missing"
    AUTHORITY_NOT_CURRENT = "authority_not_current"
    GENERATION_MISMATCH = "generation_mismatch"
    CYCLE_MISMATCH = "cycle_mismatch"
    PLAN_SET_MISMATCH = "plan_set_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    PART_MISMATCH = "part_mismatch"
    ROUND_MISMATCH = "round_mismatch"
    PARENT_MISMATCH = "parent_mismatch"
    AUTHORITY_WITHOUT_REPORT = "authority_without_report"
    INVENTORY_MISMATCH = "inventory_mismatch"
    FINDINGS_MISMATCH = "findings_mismatch"
    PLAN_MISMATCH = "plan_mismatch"
    INVENTORY_INVALID = "inventory_invalid"
    REQUIREMENTS_MAP_INVALID = "requirements_map_invalid"
    REQUIREMENT_ORDER_MISMATCH = "requirement_order_mismatch"
    DISPOSITION_MISMATCH = "disposition_mismatch"
    SATISFIED_ROUND_MISMATCH = "satisfied_round_mismatch"
    UNMAPPED_REQUIREMENT = "unmapped_requirement"
    IMPLEMENTATION_STEP_MISSING = "implementation_step_missing"
    WAIVER_NOT_FOUND = "waiver_not_found"
    WAIVER_LEDGER_INVALID = "waiver_ledger_invalid"
    WAIVER_DIGEST_MISMATCH = "waiver_digest_mismatch"
    WAIVER_SCOPE_MISMATCH = "waiver_scope_mismatch"
    WAIVER_PART_MISMATCH = "waiver_part_mismatch"
    WAIVER_EXPIRED = "waiver_expired"
    WAIVER_NOT_APPLICABLE = "waiver_not_applicable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class CarriedToStep:
    pass


@dataclass(frozen=True, slots=True)
class SatisfiedByRound:
    audit_round: int


@dataclass(frozen=True, slots=True)
class WaivedByDecision:
    waiver_id: str


def _parse_disposition(
    disposition: str,
) -> CarriedToStep | SatisfiedByRound | WaivedByDecision:
    if not isinstance(disposition, str):
        raise ValueError("disposition must be a string")
    if disposition == "carried@step":
        return CarriedToStep()
    satisfied = _SATISFIED_RE.fullmatch(disposition)
    if satisfied is not None:
        return SatisfiedByRound(int(satisfied.group(1)))
    waived = _WAIVED_RE.fullmatch(disposition)
    if waived is not None:
        return WaivedByDecision(waived.group(1))
    raise ValueError(
        "disposition must be carried@step, satisfied-by-round-N, or waived-by-decision@<id>"
    )


@dataclass(frozen=True, slots=True)
class PlanDispositionRow:
    requirement_id: str
    disposition: str
    implementation_step: str | None
    row_digest: str

    def __post_init__(self) -> None:
        _require_nonempty("PlanDispositionRow.requirement_id", self.requirement_id)
        match _parse_disposition(self.disposition):
            case CarriedToStep():
                if self.implementation_step is None:
                    raise ValueError("carried@step requires implementation_step")
                _require_nonempty(
                    "PlanDispositionRow.implementation_step", self.implementation_step
                )
            case SatisfiedByRound():
                if self.implementation_step is not None:
                    raise ValueError("satisfied-by-round-N cannot name implementation_step")
            case WaivedByDecision():
                if self.implementation_step is not None:
                    raise ValueError("waived-by-decision@<id> cannot name implementation_step")
            case unreachable:
                assert_never(unreachable)
        _require_digest("PlanDispositionRow.row_digest", self.row_digest)
        if self.row_digest != self.compute_digest():
            raise ValueError("PlanDispositionRow.row_digest does not match row content")

    @property
    def satisfied_round(self) -> int | None:
        match _parse_disposition(self.disposition):
            case SatisfiedByRound(audit_round):
                return audit_round
            case CarriedToStep() | WaivedByDecision():
                return None
            case unreachable:
                assert_never(unreachable)

    @property
    def waiver_id(self) -> str | None:
        match _parse_disposition(self.disposition):
            case WaivedByDecision(waiver_id):
                return waiver_id
            case CarriedToStep() | SatisfiedByRound():
                return None
            case unreachable:
                assert_never(unreachable)

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        disposition: str,
        implementation_step: str | None = None,
    ) -> Self:
        payload = {
            "disposition": disposition,
            "implementation_step": implementation_step,
            "requirement_id": requirement_id,
        }
        return cls(
            requirement_id=requirement_id,
            disposition=disposition,
            implementation_step=implementation_step,
            row_digest=compute_canonical_hash(payload, domain=_DISPOSITION_ROW_DOMAIN),
        )

    def compute_digest(self) -> str:
        return compute_canonical_hash(
            self.to_dict(include_digest=False), domain=_DISPOSITION_ROW_DOMAIN
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "disposition": self.disposition,
            "implementation_step": self.implementation_step,
            "requirement_id": self.requirement_id,
        }
        if include_digest:
            payload["row_digest"] = self.row_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(
                requirement_id=data["requirement_id"],
                disposition=data["disposition"],
                implementation_step=data["implementation_step"],
                row_digest=data["row_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid PlanDispositionRow: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AuditFindingWaiver:
    waiver_id: str
    requirement_id: str
    finding_row_digest: str
    plan_set_id: str
    scope_id: str
    part_id: str
    rationale: str
    issue: int
    approved_by: str
    added_date: date
    review_date: date

    def __post_init__(self) -> None:
        if _WAIVED_RE.fullmatch(f"waived-by-decision@{self.waiver_id}") is None:
            raise ValueError("AuditFindingWaiver.waiver_id has invalid format")
        for name in ("requirement_id", "plan_set_id", "scope_id", "part_id", "approved_by"):
            _require_nonempty(f"AuditFindingWaiver.{name}", getattr(self, name))
        _require_digest("AuditFindingWaiver.finding_row_digest", self.finding_row_digest)
        if (
            not isinstance(self.rationale, str)
            or len(self.rationale.strip()) < _MIN_RATIONALE_LENGTH
        ):
            raise ValueError(
                "AuditFindingWaiver.rationale must contain at least "
                f"{_MIN_RATIONALE_LENGTH} non-whitespace characters"
            )
        _require_positive_int("AuditFindingWaiver.issue", self.issue)
        for name in ("added_date", "review_date"):
            value = getattr(self, name)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise ValueError(f"AuditFindingWaiver.{name} must be a date")
        if self.review_date < self.added_date:
            raise ValueError("AuditFindingWaiver.review_date must not precede added_date")

    def is_stale(self, *, as_of: date) -> bool:
        if not isinstance(as_of, date) or isinstance(as_of, datetime):
            raise ValueError("AuditFindingWaiver.as_of must be a date")
        if self.review_date > as_of:
            raise ValueError("AuditFindingWaiver.review_date cannot be after as_of")
        return as_of - self.review_date > timedelta(days=180)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        expected_fields = {
            "waiver_id",
            "requirement_id",
            "finding_row_digest",
            "plan_set_id",
            "scope_id",
            "part_id",
            "rationale",
            "issue",
            "approved_by",
            "added_date",
            "review_date",
        }
        if set(data) != expected_fields:
            missing = sorted(expected_fields - set(data))
            unknown = sorted(set(data) - expected_fields)
            raise ValueError(
                "AuditFindingWaiver fields must match the ledger schema "
                f"(missing={missing}, unknown={unknown})"
            )
        try:
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid AuditFindingWaiver: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PlanDispositionReport:
    schema_version: int
    execution_generation: str
    cycle_id: str
    plan_set_id: str
    scope_id: str
    part_id: str
    audit_round: int
    parent_authority_digest: str
    inventory_digest: str
    findings_digest: str
    current_plan_ref: ArtifactRef
    dispositions: tuple[PlanDispositionRow, ...]
    generated_at: str
    report_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dispositions",
            _immutable_typed_tuple(
                "PlanDispositionReport.dispositions",
                self.dispositions,
                PlanDispositionRow,
            ),
        )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != AUDIT_CYCLE_SCHEMA_VERSION
        ):
            raise ValueError(
                f"PlanDispositionReport.schema_version must be {AUDIT_CYCLE_SCHEMA_VERSION}"
            )
        for name in ("execution_generation", "cycle_id", "plan_set_id", "scope_id", "part_id"):
            _require_nonempty(f"PlanDispositionReport.{name}", getattr(self, name))
        _require_positive_int("PlanDispositionReport.audit_round", self.audit_round)
        for name in ("parent_authority_digest", "inventory_digest", "findings_digest"):
            _require_digest(f"PlanDispositionReport.{name}", getattr(self, name))
        if not isinstance(self.current_plan_ref, ArtifactRef):
            raise ValueError("PlanDispositionReport.current_plan_ref must be an ArtifactRef")
        ids = tuple(row.requirement_id for row in self.dispositions)
        if len(set(ids)) != len(ids):
            raise ValueError("PlanDispositionReport.dispositions contain duplicate IDs")
        _require_nonempty("PlanDispositionReport.generated_at", self.generated_at)
        _require_digest("PlanDispositionReport.report_digest", self.report_digest)
        if self.report_digest != self.compute_digest():
            raise ValueError("PlanDispositionReport.report_digest does not match report content")

    @classmethod
    def create(
        cls,
        *,
        execution_generation: str,
        cycle_id: str,
        plan_set_id: str,
        scope_id: str,
        part_id: str,
        audit_round: int,
        parent_authority_digest: str,
        inventory_digest: str,
        findings_digest: str,
        current_plan_ref: ArtifactRef,
        dispositions: tuple[PlanDispositionRow, ...],
        generated_at: str,
    ) -> Self:
        dispositions = _immutable_typed_tuple(
            "PlanDispositionReport.dispositions",
            dispositions,
            PlanDispositionRow,
        )
        values: dict[str, Any] = {
            "audit_round": audit_round,
            "current_plan_ref": current_plan_ref.to_dict(),
            "cycle_id": cycle_id,
            "dispositions": [row.to_dict() for row in dispositions],
            "execution_generation": execution_generation,
            "findings_digest": findings_digest,
            "generated_at": generated_at,
            "inventory_digest": inventory_digest,
            "parent_authority_digest": parent_authority_digest,
            "part_id": part_id,
            "plan_set_id": plan_set_id,
            "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
            "scope_id": scope_id,
        }
        digest = compute_canonical_hash(values, domain=_REPORT_DOMAIN)
        return cls(
            schema_version=AUDIT_CYCLE_SCHEMA_VERSION,
            execution_generation=execution_generation,
            cycle_id=cycle_id,
            plan_set_id=plan_set_id,
            scope_id=scope_id,
            part_id=part_id,
            audit_round=audit_round,
            parent_authority_digest=parent_authority_digest,
            inventory_digest=inventory_digest,
            findings_digest=findings_digest,
            current_plan_ref=current_plan_ref,
            dispositions=dispositions,
            generated_at=generated_at,
            report_digest=digest,
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def compute_digest(self) -> str:
        return compute_canonical_hash(self.to_dict(include_digest=False), domain=_REPORT_DOMAIN)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "audit_round": self.audit_round,
            "current_plan_ref": self.current_plan_ref.to_dict(),
            "cycle_id": self.cycle_id,
            "dispositions": [row.to_dict() for row in self.dispositions],
            "execution_generation": self.execution_generation,
            "findings_digest": self.findings_digest,
            "generated_at": self.generated_at,
            "inventory_digest": self.inventory_digest,
            "parent_authority_digest": self.parent_authority_digest,
            "part_id": self.part_id,
            "plan_set_id": self.plan_set_id,
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
        }
        if include_digest:
            payload["report_digest"] = self.report_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(
                schema_version=data["schema_version"],
                execution_generation=data["execution_generation"],
                cycle_id=data["cycle_id"],
                plan_set_id=data["plan_set_id"],
                scope_id=data["scope_id"],
                part_id=data["part_id"],
                audit_round=data["audit_round"],
                parent_authority_digest=data["parent_authority_digest"],
                inventory_digest=data["inventory_digest"],
                findings_digest=data["findings_digest"],
                current_plan_ref=ArtifactRef.from_dict(data["current_plan_ref"]),
                dispositions=tuple(
                    PlanDispositionRow.from_dict(item) for item in data["dispositions"]
                ),
                generated_at=data["generated_at"],
                report_digest=data["report_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid PlanDispositionReport: {exc}") from exc


def _validate_admission_reason(status: AdmissionStatus, reason: AdmissionReason) -> None:
    omit_reasons = {
        AdmissionReason.NO_AUTHORITY,
        AdmissionReason.TRUSTED_GO,
        AdmissionReason.TRUSTED_GO_SUCCESSOR,
    }
    if status is AdmissionStatus.OMIT and reason not in omit_reasons:
        raise ValueError("OMIT admission requires an omission reason")
    if status is AdmissionStatus.PASS and reason is not AdmissionReason.ADMITTED:
        raise ValueError("PASS admission requires the admitted reason")
    if status is AdmissionStatus.REJECT and (
        reason is AdmissionReason.ADMITTED or reason in omit_reasons
    ):
        raise ValueError("REJECT admission requires a rejection reason")


@dataclass(frozen=True, slots=True)
class InventoryAdmissionDecision:
    status: AdmissionStatus
    reason: AdmissionReason
    dispositions: tuple[PlanDispositionRow, ...] = ()
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dispositions",
            _immutable_typed_tuple(
                "InventoryAdmissionDecision.dispositions",
                self.dispositions,
                PlanDispositionRow,
            ),
        )
        object.__setattr__(
            self,
            "details",
            _immutable_typed_tuple(
                "InventoryAdmissionDecision.details",
                self.details,
                str,
            ),
        )
        if not isinstance(self.status, AdmissionStatus):
            raise ValueError("InventoryAdmissionDecision.status must be an AdmissionStatus")
        if not isinstance(self.reason, AdmissionReason):
            raise ValueError("InventoryAdmissionDecision.reason must be an AdmissionReason")
        _validate_admission_reason(self.status, self.reason)
        if self.status is AdmissionStatus.OMIT:
            if self.dispositions or self.details:
                raise ValueError("OMIT admission cannot carry payload")
        elif self.status is AdmissionStatus.PASS:
            if self.details:
                raise ValueError("PASS admission cannot carry rejection details")
        else:
            if self.dispositions:
                raise ValueError("REJECT admission cannot carry disposition rows")
            if not self.details or any(
                not isinstance(detail, str) or not detail.strip() for detail in self.details
            ):
                raise ValueError("REJECT admission requires non-empty rejection details")

    @classmethod
    def omit(cls, reason: AdmissionReason) -> Self:
        return cls(status=AdmissionStatus.OMIT, reason=reason)

    @classmethod
    def reject(cls, reason: AdmissionReason, detail: str) -> Self:
        return cls(
            status=AdmissionStatus.REJECT,
            reason=reason,
            details=(detail,),
        )

    @classmethod
    def admitted(cls, dispositions: tuple[PlanDispositionRow, ...]) -> Self:
        return cls(
            status=AdmissionStatus.PASS,
            reason=AdmissionReason.ADMITTED,
            dispositions=dispositions,
        )

"""Immutable audit-cycle plan-disposition value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from ..closure_hashing import canonical_json_bytes, compute_canonical_hash
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
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class PlanDispositionRow:
    requirement_id: str
    disposition: str
    implementation_step: str | None
    row_digest: str

    def __post_init__(self) -> None:
        _require_nonempty("PlanDispositionRow.requirement_id", self.requirement_id)
        if self.disposition == "carried@step":
            if self.implementation_step is None:
                raise ValueError("carried@step requires implementation_step")
            _require_nonempty("PlanDispositionRow.implementation_step", self.implementation_step)
        elif _SATISFIED_RE.fullmatch(self.disposition):
            if self.implementation_step is not None:
                raise ValueError("satisfied-by-round-N cannot name implementation_step")
        else:
            raise ValueError("disposition must be carried@step or satisfied-by-round-N")
        _require_digest("PlanDispositionRow.row_digest", self.row_digest)
        if self.row_digest != self.compute_digest():
            raise ValueError("PlanDispositionRow.row_digest does not match row content")

    @property
    def satisfied_round(self) -> int | None:
        matched = _SATISFIED_RE.fullmatch(self.disposition)
        return int(matched.group(1)) if matched is not None else None

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
        omit_reasons = {
            AdmissionReason.NO_AUTHORITY,
            AdmissionReason.TRUSTED_GO,
            AdmissionReason.TRUSTED_GO_SUCCESSOR,
        }
        if self.status is AdmissionStatus.OMIT:
            if self.reason not in omit_reasons:
                raise ValueError("OMIT admission requires an omission reason")
            if self.dispositions or self.details:
                raise ValueError("OMIT admission cannot carry payload")
        elif self.status is AdmissionStatus.PASS:
            if self.reason is not AdmissionReason.ADMITTED:
                raise ValueError("PASS admission requires the admitted reason")
            if self.details:
                raise ValueError("PASS admission cannot carry rejection details")
        else:
            if self.reason is AdmissionReason.ADMITTED or self.reason in omit_reasons:
                raise ValueError("REJECT admission requires a rejection reason")
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

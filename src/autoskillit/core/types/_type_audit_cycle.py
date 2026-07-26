"""Immutable audit-cycle authority and inventory-admission value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self, TypeVar

from ..closure_hashing import HASH_RE, canonical_json_bytes, compute_canonical_hash

__all__ = [
    "AUDIT_CYCLE_SCHEMA_VERSION",
    "AdmissionReason",
    "AdmissionStatus",
    "ArtifactRef",
    "AuditAssessment",
    "AuditAssessmentRow",
    "AuditCycleAuthority",
    "AuditCycleHead",
    "AuditVerdict",
    "InventoryAdmissionDecision",
    "PlanDispositionReport",
    "PlanDispositionRow",
    "compute_findings_digest",
]

AUDIT_CYCLE_SCHEMA_VERSION = 1

_ASSESSMENT_ROW_DOMAIN = "autoskillit:audit-cycle:assessment-row:v1:sha256"
_AUTHORITY_DOMAIN = "autoskillit:audit-cycle:authority:v1:sha256"
_DISPOSITION_ROW_DOMAIN = "autoskillit:audit-cycle:disposition-row:v1:sha256"
_FINDINGS_DOMAIN = "autoskillit:audit-cycle:findings:v1:sha256"
_REPORT_DOMAIN = "autoskillit:audit-cycle:plan-disposition:v1:sha256"
_SATISFIED_RE = re.compile(r"^satisfied-by-round-([1-9][0-9]*)$")
_T = TypeVar("_T")


def _immutable_typed_tuple(
    name: str,
    values: object,
    item_type: type[_T],
) -> tuple[_T, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a tuple")
    normalized = tuple(values)
    if not all(isinstance(value, item_type) for value in normalized):
        raise ValueError(f"{name} must contain only {item_type.__name__} values")
    return normalized


class AuditVerdict(StrEnum):
    GO = "GO"
    NO_GO = "NO GO"


class AuditAssessment(StrEnum):
    COVERED = "COVERED"
    MISSING = "MISSING"
    ODD = "ODD"
    CONFLICT = "CONFLICT"
    NAMED_DEVIATION = "NAMED_DEVIATION"

    @property
    def blocking(self) -> bool:
        return self in {AuditAssessment.MISSING, AuditAssessment.CONFLICT}


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


def _require_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_digest(name: str, value: str) -> None:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an algorithm-qualified sha256 digest")


def _require_positive_int(name: str, value: int, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Hash identity plus an independently enforceable absolute locator policy."""

    locator: str = field(compare=False)
    media_type: str = field(compare=False)
    schema_version: int = field(compare=False)
    byte_size: int = field(compare=False)
    content_digest: str

    def __post_init__(self) -> None:
        _require_nonempty("ArtifactRef.locator", self.locator)
        locator = Path(self.locator)
        if not locator.is_absolute() or ".." in locator.parts:
            raise ValueError("ArtifactRef.locator must be an absolute non-traversing path")
        _require_nonempty("ArtifactRef.media_type", self.media_type)
        _require_positive_int("ArtifactRef.schema_version", self.schema_version)
        _require_positive_int("ArtifactRef.byte_size", self.byte_size, allow_zero=True)
        _require_digest("ArtifactRef.content_digest", self.content_digest)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def metadata_digest(self) -> str:
        return compute_canonical_hash(
            self.to_dict(),
            domain=f"autoskillit:audit-cycle:artifact-ref:v{self.schema_version}:sha256",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "content_digest": self.content_digest,
            "locator": self.locator,
            "media_type": self.media_type,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(
                locator=data["locator"],
                media_type=data["media_type"],
                schema_version=data["schema_version"],
                byte_size=data["byte_size"],
                content_digest=data["content_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid ArtifactRef: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AuditAssessmentRow:
    requirement_id: str
    requirement_text: str
    assessment: AuditAssessment
    evidence_summary: str
    row_digest: str

    def __post_init__(self) -> None:
        _require_nonempty("AuditAssessmentRow.requirement_id", self.requirement_id)
        _require_nonempty("AuditAssessmentRow.requirement_text", self.requirement_text)
        if not isinstance(self.assessment, AuditAssessment):
            raise ValueError("AuditAssessmentRow.assessment must be an AuditAssessment")
        _require_nonempty("AuditAssessmentRow.evidence_summary", self.evidence_summary)
        _require_digest("AuditAssessmentRow.row_digest", self.row_digest)
        if self.row_digest != self.compute_digest():
            raise ValueError("AuditAssessmentRow.row_digest does not match row content")

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        requirement_text: str,
        assessment: AuditAssessment,
        evidence_summary: str,
    ) -> Self:
        payload = {
            "assessment": assessment.value,
            "evidence_summary": evidence_summary,
            "requirement_id": requirement_id,
            "requirement_text": requirement_text,
        }
        return cls(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            assessment=assessment,
            evidence_summary=evidence_summary,
            row_digest=compute_canonical_hash(payload, domain=_ASSESSMENT_ROW_DOMAIN),
        )

    def compute_digest(self) -> str:
        return compute_canonical_hash(
            self.to_dict(include_digest=False), domain=_ASSESSMENT_ROW_DOMAIN
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assessment": self.assessment.value,
            "evidence_summary": self.evidence_summary,
            "requirement_id": self.requirement_id,
            "requirement_text": self.requirement_text,
        }
        if include_digest:
            payload["row_digest"] = self.row_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(
                requirement_id=data["requirement_id"],
                requirement_text=data["requirement_text"],
                assessment=AuditAssessment(data["assessment"]),
                evidence_summary=data["evidence_summary"],
                row_digest=data["row_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid AuditAssessmentRow: {exc}") from exc


def compute_findings_digest(rows: tuple[AuditAssessmentRow, ...]) -> str:
    return compute_canonical_hash(
        {"blocking_row_digests": [row.row_digest for row in rows if row.assessment.blocking]},
        domain=_FINDINGS_DOMAIN,
    )


@dataclass(frozen=True, slots=True)
class AuditCycleAuthority:
    schema_version: int
    execution_generation: str
    cycle_id: str
    plan_set_id: str
    scope_id: str
    part_id: str
    audit_round: int
    parent_authority_digest: str | None
    audited_plan_refs: tuple[ArtifactRef, ...]
    inventory_ref: ArtifactRef
    findings_digest: str
    assessments: tuple[AuditAssessmentRow, ...]
    verdict: AuditVerdict
    remediation_ref: ArtifactRef | None
    generated_at: str
    authority_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "audited_plan_refs",
            _immutable_typed_tuple(
                "AuditCycleAuthority.audited_plan_refs",
                self.audited_plan_refs,
                ArtifactRef,
            ),
        )
        object.__setattr__(
            self,
            "assessments",
            _immutable_typed_tuple(
                "AuditCycleAuthority.assessments",
                self.assessments,
                AuditAssessmentRow,
            ),
        )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != AUDIT_CYCLE_SCHEMA_VERSION
        ):
            raise ValueError(
                f"AuditCycleAuthority.schema_version must be {AUDIT_CYCLE_SCHEMA_VERSION}"
            )
        for name in ("execution_generation", "cycle_id", "plan_set_id", "scope_id", "part_id"):
            _require_nonempty(f"AuditCycleAuthority.{name}", getattr(self, name))
        _require_positive_int("AuditCycleAuthority.audit_round", self.audit_round)
        if self.parent_authority_digest is not None:
            _require_digest(
                "AuditCycleAuthority.parent_authority_digest",
                self.parent_authority_digest,
            )
        if not self.audited_plan_refs:
            raise ValueError("AuditCycleAuthority.audited_plan_refs must be non-empty")
        if len({ref.content_digest for ref in self.audited_plan_refs}) != len(
            self.audited_plan_refs
        ):
            raise ValueError("AuditCycleAuthority.audited_plan_refs contain duplicate content")
        if not isinstance(self.inventory_ref, ArtifactRef):
            raise ValueError("AuditCycleAuthority.inventory_ref must be an ArtifactRef")
        if self.remediation_ref is not None and not isinstance(self.remediation_ref, ArtifactRef):
            raise ValueError("AuditCycleAuthority.remediation_ref must be an ArtifactRef")
        ids = tuple(row.requirement_id for row in self.assessments)
        if len(set(ids)) != len(ids):
            raise ValueError("AuditCycleAuthority.assessments contain duplicate requirement IDs")
        _require_digest("AuditCycleAuthority.findings_digest", self.findings_digest)
        if self.findings_digest != compute_findings_digest(self.assessments):
            raise ValueError("AuditCycleAuthority.findings_digest does not match assessments")
        if not isinstance(self.verdict, AuditVerdict):
            raise ValueError("AuditCycleAuthority.verdict must be an AuditVerdict")
        if self.verdict is AuditVerdict.NO_GO and self.remediation_ref is None:
            raise ValueError("NO GO authority requires remediation_ref")
        if self.verdict is AuditVerdict.GO and self.remediation_ref is not None:
            raise ValueError("GO authority cannot contain remediation_ref")
        if self.verdict is AuditVerdict.GO and any(
            row.assessment.blocking for row in self.assessments
        ):
            raise ValueError("GO authority cannot contain blocking assessments")
        _require_nonempty("AuditCycleAuthority.generated_at", self.generated_at)
        _require_digest("AuditCycleAuthority.authority_digest", self.authority_digest)
        if self.authority_digest != self.compute_digest():
            raise ValueError("AuditCycleAuthority.authority_digest does not match event content")

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
        parent_authority_digest: str | None,
        audited_plan_refs: tuple[ArtifactRef, ...],
        inventory_ref: ArtifactRef,
        assessments: tuple[AuditAssessmentRow, ...],
        verdict: AuditVerdict,
        remediation_ref: ArtifactRef | None,
        generated_at: str,
    ) -> Self:
        audited_plan_refs = _immutable_typed_tuple(
            "AuditCycleAuthority.audited_plan_refs",
            audited_plan_refs,
            ArtifactRef,
        )
        assessments = _immutable_typed_tuple(
            "AuditCycleAuthority.assessments",
            assessments,
            AuditAssessmentRow,
        )
        values: dict[str, Any] = {
            "assessments": [row.to_dict() for row in assessments],
            "audit_round": audit_round,
            "audited_plan_refs": [ref.to_dict() for ref in audited_plan_refs],
            "cycle_id": cycle_id,
            "execution_generation": execution_generation,
            "findings_digest": compute_findings_digest(assessments),
            "generated_at": generated_at,
            "inventory_ref": inventory_ref.to_dict(),
            "parent_authority_digest": parent_authority_digest,
            "part_id": part_id,
            "plan_set_id": plan_set_id,
            "remediation_ref": remediation_ref.to_dict() if remediation_ref else None,
            "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
            "scope_id": scope_id,
            "verdict": verdict.value,
        }
        digest = compute_canonical_hash(values, domain=_AUTHORITY_DOMAIN)
        return cls(
            schema_version=AUDIT_CYCLE_SCHEMA_VERSION,
            execution_generation=execution_generation,
            cycle_id=cycle_id,
            plan_set_id=plan_set_id,
            scope_id=scope_id,
            part_id=part_id,
            audit_round=audit_round,
            parent_authority_digest=parent_authority_digest,
            audited_plan_refs=audited_plan_refs,
            inventory_ref=inventory_ref,
            findings_digest=values["findings_digest"],
            assessments=assessments,
            verdict=verdict,
            remediation_ref=remediation_ref,
            generated_at=generated_at,
            authority_digest=digest,
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def compute_digest(self) -> str:
        return compute_canonical_hash(self.to_dict(include_digest=False), domain=_AUTHORITY_DOMAIN)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assessments": [row.to_dict() for row in self.assessments],
            "audit_round": self.audit_round,
            "audited_plan_refs": [ref.to_dict() for ref in self.audited_plan_refs],
            "cycle_id": self.cycle_id,
            "execution_generation": self.execution_generation,
            "findings_digest": self.findings_digest,
            "generated_at": self.generated_at,
            "inventory_ref": self.inventory_ref.to_dict(),
            "parent_authority_digest": self.parent_authority_digest,
            "part_id": self.part_id,
            "plan_set_id": self.plan_set_id,
            "remediation_ref": (
                self.remediation_ref.to_dict() if self.remediation_ref is not None else None
            ),
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "verdict": self.verdict.value,
        }
        if include_digest:
            payload["authority_digest"] = self.authority_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            remediation_raw = data["remediation_ref"]
            return cls(
                schema_version=data["schema_version"],
                execution_generation=data["execution_generation"],
                cycle_id=data["cycle_id"],
                plan_set_id=data["plan_set_id"],
                scope_id=data["scope_id"],
                part_id=data["part_id"],
                audit_round=data["audit_round"],
                parent_authority_digest=data["parent_authority_digest"],
                audited_plan_refs=tuple(
                    ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]
                ),
                inventory_ref=ArtifactRef.from_dict(data["inventory_ref"]),
                findings_digest=data["findings_digest"],
                assessments=tuple(
                    AuditAssessmentRow.from_dict(item) for item in data["assessments"]
                ),
                verdict=AuditVerdict(data["verdict"]),
                remediation_ref=(
                    ArtifactRef.from_dict(remediation_raw) if remediation_raw is not None else None
                ),
                generated_at=data["generated_at"],
                authority_digest=data["authority_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid AuditCycleAuthority: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AuditCycleHead:
    execution_generation: str
    cycle_id: str
    plan_set_id: str
    scope_id: str
    part_id: str
    current_authority_digest: str
    audit_round: int
    audited_plan_refs: tuple[ArtifactRef, ...]
    inventory_ref: ArtifactRef
    verdict: AuditVerdict
    authorized_successor_part_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("execution_generation", "cycle_id", "plan_set_id", "scope_id", "part_id"):
            _require_nonempty(f"AuditCycleHead.{name}", getattr(self, name))
        _require_digest("AuditCycleHead.current_authority_digest", self.current_authority_digest)
        _require_positive_int("AuditCycleHead.audit_round", self.audit_round)
        audited_plan_refs = tuple(self.audited_plan_refs)
        if not audited_plan_refs or any(
            not isinstance(ref, ArtifactRef) for ref in audited_plan_refs
        ):
            raise ValueError("AuditCycleHead.audited_plan_refs must contain ArtifactRef entries")
        if not isinstance(self.inventory_ref, ArtifactRef):
            raise ValueError("AuditCycleHead.inventory_ref must be an ArtifactRef")
        object.__setattr__(self, "audited_plan_refs", audited_plan_refs)
        if not isinstance(self.verdict, AuditVerdict):
            raise ValueError("AuditCycleHead.verdict must be an AuditVerdict")
        if self.authorized_successor_part_id is not None:
            _require_nonempty(
                "AuditCycleHead.authorized_successor_part_id",
                self.authorized_successor_part_id,
            )
            if self.verdict is not AuditVerdict.GO:
                raise ValueError("only a GO head may authorize a successor part")


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

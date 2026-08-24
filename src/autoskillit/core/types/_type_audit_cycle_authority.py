"""Immutable audit-cycle authority value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self, TypeVar

from ..closure_hashing import canonical_json_bytes, compute_canonical_hash
from ._type_audit_admission_validation import (
    _require_digest,
    _require_nonempty,
    _require_positive_int,
)
from ._type_audit_artifact_ref import ArtifactRef

__all__ = [
    "AUDIT_CYCLE_SCHEMA_VERSION",
    "AuditAssessment",
    "AuditAssessmentRow",
    "AuditCycleAuthority",
    "AuditCycleHead",
    "AuditVerdict",
    "compute_findings_digest",
    "_MAX_REFERENCED_ARTIFACTS_PER_CALL",
]

AUDIT_CYCLE_SCHEMA_VERSION = 1

_ASSESSMENT_ROW_DOMAIN = "autoskillit:audit-cycle:assessment-row:v1:sha256"
_AUTHORITY_DOMAIN = "autoskillit:audit-cycle:authority:v1:sha256"
_FINDINGS_DOMAIN = "autoskillit:audit-cycle:findings:v1:sha256"
_T = TypeVar("_T")

# Bounds the number of referenced-artifact entries accepted by one admitted
# materialization. Distinct from _MAX_ASSOCIATION_FILES, which bounds the count
# of association files already on disk under an associations/ directory glob.
_MAX_REFERENCED_ARTIFACTS_PER_CALL = 256


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

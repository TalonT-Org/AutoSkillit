"""Immutable value contracts for server-owned audit admission.

These contracts are intentionally dormant.  They describe the authority
materialization boundary without changing the protocol-v1 authority or report
wire shapes in :mod:`_type_audit_cycle`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..closure_hashing import (
    HASH_RE,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
)
from ._type_audit_cycle import (
    ArtifactRef,
    AuditAssessmentRow,
    AuditCycleHead,
    AuditVerdict,
)

__all__ = [
    "AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY",
    "AUDIT_REFERENCE_IDENTITY_PROFILE_V1",
    "AUDIT_SEMANTIC_SCHEMA_VERSION",
    "STANDALONE_AUDIT_EVIDENCE_KIND",
    "STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION",
    "AuditArtifactFieldOwnership",
    "AuditArtifactFieldOwnershipDef",
    "AuditAttemptId",
    "AuditAttemptLifecycle",
    "AuditAttemptRecord",
    "AuditIdentityReservation",
    "AuditMaterializationResult",
    "AuditMaterializationStatus",
    "AuditOutcome",
    "AuditOutcomeStatus",
    "AuditPreparedEffect",
    "AuditPreparedEffectDeliveryStatus",
    "AuditReferenceIdentityProfileDef",
    "AuditRound",
    "AuditSemanticResult",
    "AuditSlotId",
    "AuditSlotKey",
    "InstallationVersion",
    "RecipeExecutionId",
    "ReservationDecision",
    "StandaloneAuditEvidence",
    "compute_audit_reference_identity",
]

AUDIT_SEMANTIC_SCHEMA_VERSION = 1
STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION = 1
STANDALONE_AUDIT_EVIDENCE_KIND = "standalone_audit_evidence"

_REFERENCE_IDENTITY_DOMAIN = "autoskillit:audit-admission:ordered-full-reference:v1:sha256"
_FULL_REFERENCE_FIELDS = (
    "locator",
    "media_type",
    "schema_version",
    "byte_size",
    "content_digest",
)


class AuditArtifactFieldOwnership(StrEnum):
    """The sole authority allowed to supply one artifact field."""

    CHILD_SEMANTIC = "CHILD_SEMANTIC"
    SERVER_INJECTED = "SERVER_INJECTED"
    SERVER_DERIVED = "SERVER_DERIVED"
    VERIFIED_COPY = "VERIFIED_COPY"


class AuditMaterializationStatus(StrEnum):
    """Internal result status before caller-visible response commitment."""

    PUBLISHED_PENDING_FINALIZATION = "PUBLISHED_PENDING_FINALIZATION"
    EXACT_REPLAY = "EXACT_REPLAY"
    SEMANTIC_REJECTED = "SEMANTIC_REJECTED"
    CONFLICT = "CONFLICT"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    QUARANTINED = "QUARANTINED"
    NON_PUBLISHED_STANDALONE = "NON_PUBLISHED_STANDALONE"


class AuditOutcomeStatus(StrEnum):
    """Caller-visible audit outcome after response commitment."""

    PUBLISHED = "PUBLISHED"
    EXACT_REPLAY = "EXACT_REPLAY"
    SEMANTIC_REJECTED = "SEMANTIC_REJECTED"
    CONFLICT = "CONFLICT"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    QUARANTINED = "QUARANTINED"
    NON_PUBLISHED_STANDALONE = "NON_PUBLISHED_STANDALONE"


class ReservationDecision(StrEnum):
    DISPATCH_NEW = "DISPATCH_NEW"
    REDISPATCH_OPEN = "REDISPATCH_OPEN"
    RESUME_PREPARED = "RESUME_PREPARED"
    PUBLISHED_PENDING_FINALIZATION = "PUBLISHED_PENDING_FINALIZATION"
    EXACT_REPLAY = "EXACT_REPLAY"
    CONFLICT = "CONFLICT"


class AuditAttemptLifecycle(StrEnum):
    OPEN = "OPEN"
    SEMANTIC_ACCEPTED = "SEMANTIC_ACCEPTED"
    SEMANTIC_REJECTED = "SEMANTIC_REJECTED"
    PREPARED = "PREPARED"
    PUBLISHED_PENDING_FINALIZATION = "PUBLISHED_PENDING_FINALIZATION"
    RESPONSE_COMMITTED = "RESPONSE_COMMITTED"
    CONFLICT = "CONFLICT"
    QUARANTINED = "QUARANTINED"


class AuditPreparedEffectDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    QUARANTINED = "QUARANTINED"


def _require_nonempty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an algorithm-qualified sha256 digest")
    return value


def _require_optional_digest(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_digest(name, value)


def _require_absolute_path(name: str, value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{name} must be an absolute non-traversing Path")
    return value


def _typed_tuple(name: str, value: object, item_type: type[Any]) -> tuple[Any, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise ValueError(f"{name} must be a tuple of {item_type.__name__} values")
    return value


def _full_reference_key(reference: ArtifactRef) -> tuple[object, ...]:
    payload = reference.to_dict()
    return tuple(payload[field_name] for field_name in _FULL_REFERENCE_FIELDS)


def _validate_semantic_fields(
    *,
    owner: str,
    schema_version: object,
    audited_plan_refs: object,
    assessments: object,
    verdict: object,
    remediation_ref: object,
    expected_schema_version: int,
) -> None:
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != expected_schema_version
    ):
        raise ValueError(f"{owner}.schema_version must be {expected_schema_version}")
    refs = _typed_tuple(f"{owner}.audited_plan_refs", audited_plan_refs, ArtifactRef)
    if not refs:
        raise ValueError(f"{owner}.audited_plan_refs must be non-empty")
    full_reference_keys = tuple(_full_reference_key(reference) for reference in refs)
    if len(set(full_reference_keys)) != len(full_reference_keys):
        raise ValueError(f"{owner}.audited_plan_refs contain duplicate full references")
    rows = _typed_tuple(f"{owner}.assessments", assessments, AuditAssessmentRow)
    requirement_ids = tuple(row.requirement_id for row in rows)
    if len(set(requirement_ids)) != len(requirement_ids):
        raise ValueError(f"{owner}.assessments contain duplicate requirement IDs")
    if not isinstance(verdict, AuditVerdict):
        raise ValueError(f"{owner}.verdict must be an AuditVerdict")
    if remediation_ref is not None and not isinstance(remediation_ref, ArtifactRef):
        raise ValueError(f"{owner}.remediation_ref must be an ArtifactRef or None")
    if verdict is AuditVerdict.GO:
        if remediation_ref is not None:
            raise ValueError(f"{owner} GO verdict cannot carry remediation_ref")
        if any(row.assessment.blocking for row in rows):
            raise ValueError(f"{owner} GO verdict cannot carry blocking assessments")
    elif remediation_ref is None:
        raise ValueError(f"{owner} NO GO verdict requires remediation_ref")


def _semantic_payload(
    *,
    schema_version: int,
    audited_plan_refs: tuple[ArtifactRef, ...],
    assessments: tuple[AuditAssessmentRow, ...],
    verdict: AuditVerdict,
    remediation_ref: ArtifactRef | None,
) -> dict[str, Any]:
    return {
        "assessments": [row.to_dict() for row in assessments],
        "audited_plan_refs": [reference.to_dict() for reference in audited_plan_refs],
        "remediation_ref": (remediation_ref.to_dict() if remediation_ref is not None else None),
        "schema_version": schema_version,
        "verdict": verdict.value,
    }


@dataclass(frozen=True, slots=True)
class AuditArtifactFieldOwnershipDef:
    artifact_kind: str
    field_name: str
    ownership: AuditArtifactFieldOwnership

    def __post_init__(self) -> None:
        _require_nonempty("AuditArtifactFieldOwnershipDef.artifact_kind", self.artifact_kind)
        _require_nonempty("AuditArtifactFieldOwnershipDef.field_name", self.field_name)
        if not isinstance(self.ownership, AuditArtifactFieldOwnership):
            raise ValueError(
                "AuditArtifactFieldOwnershipDef.ownership must be an AuditArtifactFieldOwnership"
            )


def _ownership_registry() -> Mapping[tuple[str, str], AuditArtifactFieldOwnershipDef]:
    definitions: list[AuditArtifactFieldOwnershipDef] = []
    groups: dict[
        str,
        dict[AuditArtifactFieldOwnership, tuple[str, ...]],
    ] = {
        "semantic_result": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "schema_version",
                "audited_plan_refs",
                "assessments",
                "verdict",
                "remediation_ref",
            ),
        },
        "standalone_evidence": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "schema_version",
                "audited_plan_refs",
                "assessments",
                "verdict",
                "remediation_ref",
            ),
            AuditArtifactFieldOwnership.SERVER_DERIVED: ("kind",),
        },
        "authority": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "assessments",
                "verdict",
                "remediation_ref",
            ),
            AuditArtifactFieldOwnership.SERVER_INJECTED: (
                "execution_generation",
                "cycle_id",
                "scope_id",
                "part_id",
                "audit_round",
                "generated_at",
            ),
            AuditArtifactFieldOwnership.SERVER_DERIVED: (
                "schema_version",
                "plan_set_id",
                "inventory_ref",
                "findings_digest",
                "authority_digest",
            ),
            AuditArtifactFieldOwnership.VERIFIED_COPY: (
                "parent_authority_digest",
                "audited_plan_refs",
            ),
        },
    }
    for artifact_kind, ownership_groups in groups.items():
        for ownership, field_names in ownership_groups.items():
            definitions.extend(
                AuditArtifactFieldOwnershipDef(
                    artifact_kind=artifact_kind,
                    field_name=field_name,
                    ownership=ownership,
                )
                for field_name in field_names
            )
    return MappingProxyType(
        {
            (definition.artifact_kind, definition.field_name): definition
            for definition in definitions
        }
    )


AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY = _ownership_registry()


@dataclass(frozen=True, slots=True)
class AuditReferenceIdentityProfileDef:
    profile_id: str
    reference_fields: tuple[str, ...]
    domain: str
    canonical_json_required: bool
    plan_set_domain: str
    digest_algorithm_allowlist: tuple[str, ...]
    verifier_byte_limit: int
    resolved_absolute_contained_paths_required: bool
    uri_locators_allowed: bool
    traversal_allowed: bool
    symlinks_allowed: bool
    hardlinks_allowed: bool
    world_writable_allowed: bool
    reject_duplicate_canonical_records: bool
    reject_duplicate_resolved_locators: bool
    ordered_sequence: bool

    def __post_init__(self) -> None:
        _require_nonempty("AuditReferenceIdentityProfileDef.profile_id", self.profile_id)
        if (
            not isinstance(self.reference_fields, tuple)
            or self.reference_fields != _FULL_REFERENCE_FIELDS
        ):
            raise ValueError("audit reference identity profile must cover the full reference")
        _require_nonempty("AuditReferenceIdentityProfileDef.domain", self.domain)
        _require_nonempty(
            "AuditReferenceIdentityProfileDef.plan_set_domain",
            self.plan_set_domain,
        )
        if self.canonical_json_required is not True:
            raise ValueError("audit reference identity requires canonical JSON")
        if self.digest_algorithm_allowlist != ("sha256",):
            raise ValueError("audit reference identity permits only sha256")
        if (
            isinstance(self.verifier_byte_limit, bool)
            or not isinstance(self.verifier_byte_limit, int)
            or self.verifier_byte_limit < 1
        ):
            raise ValueError(
                "AuditReferenceIdentityProfileDef.verifier_byte_limit must be positive"
            )
        required_true = (
            self.resolved_absolute_contained_paths_required,
            self.reject_duplicate_canonical_records,
            self.reject_duplicate_resolved_locators,
            self.ordered_sequence,
        )
        required_false = (
            self.uri_locators_allowed,
            self.traversal_allowed,
            self.symlinks_allowed,
            self.hardlinks_allowed,
            self.world_writable_allowed,
        )
        if not all(value is True for value in required_true) or not all(
            value is False for value in required_false
        ):
            raise ValueError("audit reference identity path policy is not strict")


AUDIT_REFERENCE_IDENTITY_PROFILE_V1 = AuditReferenceIdentityProfileDef(
    profile_id="ordered-full-reference-v1",
    reference_fields=_FULL_REFERENCE_FIELDS,
    domain=_REFERENCE_IDENTITY_DOMAIN,
    canonical_json_required=True,
    plan_set_domain="audit-plan-set-v1",
    digest_algorithm_allowlist=("sha256",),
    verifier_byte_limit=10_000_000,
    resolved_absolute_contained_paths_required=True,
    uri_locators_allowed=False,
    traversal_allowed=False,
    symlinks_allowed=False,
    hardlinks_allowed=False,
    world_writable_allowed=False,
    reject_duplicate_canonical_records=True,
    reject_duplicate_resolved_locators=True,
    ordered_sequence=True,
)


def compute_audit_reference_identity(
    references: tuple[ArtifactRef, ...],
    *,
    profile: AuditReferenceIdentityProfileDef = AUDIT_REFERENCE_IDENTITY_PROFILE_V1,
) -> str:
    """Hash exact, ordered reference metadata under the declared profile."""

    if not isinstance(profile, AuditReferenceIdentityProfileDef):
        raise ValueError("profile must be an AuditReferenceIdentityProfileDef")
    refs = _typed_tuple("references", references, ArtifactRef)
    if not refs:
        raise ValueError("references must be non-empty")
    canonical_records: list[bytes] = []
    normalized_locators: list[str] = []
    for reference in refs:
        payload = reference.to_dict()
        locator = reference.locator
        normalized_locator = str(Path(locator))
        if (
            "://" in locator
            or not Path(locator).is_absolute()
            or ".." in Path(locator).parts
            or normalized_locator != locator
        ):
            raise ValueError("reference locator must be absolute, normalized, and non-URI")
        algorithm, separator, _ = reference.content_digest.partition(":")
        if not separator or algorithm not in profile.digest_algorithm_allowlist:
            raise ValueError("reference digest algorithm is not allowed")
        if reference.byte_size > profile.verifier_byte_limit:
            raise ValueError("reference byte_size exceeds verifier limit")
        canonical_records.append(canonical_json_bytes(payload))
        normalized_locators.append(normalized_locator)
    if len(set(canonical_records)) != len(canonical_records):
        raise ValueError("references contain duplicate canonical records")
    if len(set(normalized_locators)) != len(normalized_locators):
        raise ValueError("references contain duplicate resolved locators")
    return compute_canonical_hash(
        {
            "profile_id": profile.profile_id,
            "plan_set_domain": profile.plan_set_domain,
            "references": [
                {
                    field_name: reference.to_dict()[field_name]
                    for field_name in profile.reference_fields
                }
                for reference in refs
            ],
        },
        domain=profile.domain,
    )


@dataclass(frozen=True, slots=True)
class AuditSemanticResult:
    schema_version: int
    audited_plan_refs: tuple[ArtifactRef, ...]
    assessments: tuple[AuditAssessmentRow, ...]
    verdict: AuditVerdict
    remediation_ref: ArtifactRef | None

    def __post_init__(self) -> None:
        _validate_semantic_fields(
            owner="AuditSemanticResult",
            schema_version=self.schema_version,
            audited_plan_refs=self.audited_plan_refs,
            assessments=self.assessments,
            verdict=self.verdict,
            remediation_ref=self.remediation_ref,
            expected_schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return _semantic_payload(
            schema_version=self.schema_version,
            audited_plan_refs=self.audited_plan_refs,
            assessments=self.assessments,
            verdict=self.verdict,
            remediation_ref=self.remediation_ref,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuditSemanticResult:
        if not isinstance(data, Mapping) or set(data) != {
            "schema_version",
            "audited_plan_refs",
            "assessments",
            "verdict",
            "remediation_ref",
        }:
            raise ValueError("invalid AuditSemanticResult fields")
        try:
            remediation = data["remediation_ref"]
            return cls(
                schema_version=data["schema_version"],
                audited_plan_refs=tuple(
                    ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]
                ),
                assessments=tuple(
                    AuditAssessmentRow.from_dict(item) for item in data["assessments"]
                ),
                verdict=AuditVerdict(data["verdict"]),
                remediation_ref=(
                    ArtifactRef.from_dict(remediation) if remediation is not None else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid AuditSemanticResult: {exc}") from exc


@dataclass(frozen=True, slots=True)
class StandaloneAuditEvidence:
    schema_version: int
    kind: str
    audited_plan_refs: tuple[ArtifactRef, ...]
    assessments: tuple[AuditAssessmentRow, ...]
    verdict: AuditVerdict
    remediation_ref: ArtifactRef | None

    def __post_init__(self) -> None:
        if self.kind != STANDALONE_AUDIT_EVIDENCE_KIND:
            raise ValueError(
                f"StandaloneAuditEvidence.kind must be {STANDALONE_AUDIT_EVIDENCE_KIND!r}"
            )
        _validate_semantic_fields(
            owner="StandaloneAuditEvidence",
            schema_version=self.schema_version,
            audited_plan_refs=self.audited_plan_refs,
            assessments=self.assessments,
            verdict=self.verdict,
            remediation_ref=self.remediation_ref,
            expected_schema_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            **_semantic_payload(
                schema_version=self.schema_version,
                audited_plan_refs=self.audited_plan_refs,
                assessments=self.assessments,
                verdict=self.verdict,
                remediation_ref=self.remediation_ref,
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StandaloneAuditEvidence:
        if not isinstance(data, Mapping) or set(data) != {
            "schema_version",
            "kind",
            "audited_plan_refs",
            "assessments",
            "verdict",
            "remediation_ref",
        }:
            raise ValueError("invalid StandaloneAuditEvidence fields")
        try:
            semantic = AuditSemanticResult.from_dict(
                {key: value for key, value in data.items() if key != "kind"}
            )
            return cls(
                schema_version=semantic.schema_version,
                kind=data["kind"],
                audited_plan_refs=semantic.audited_plan_refs,
                assessments=semantic.assessments,
                verdict=semantic.verdict,
                remediation_ref=semantic.remediation_ref,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid StandaloneAuditEvidence: {exc}") from exc


@dataclass(frozen=True, slots=True)
class _OpaqueString:
    value: str

    def __post_init__(self) -> None:
        _require_nonempty(f"{type(self).__name__}.value", self.value)


@dataclass(frozen=True, slots=True)
class RecipeExecutionId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class InstallationVersion(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AuditSlotId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AuditAttemptId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AuditRound:
    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise ValueError("AuditRound.value must be a positive integer")


@dataclass(frozen=True, slots=True)
class AuditSlotKey:
    recipe_execution_id: RecipeExecutionId
    installation_version: InstallationVersion
    step_name: str
    invocation_template_digest: str
    slot_intent_digest: str
    ordered_reference_identity: str
    prior_authority_digest: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.recipe_execution_id, RecipeExecutionId):
            raise ValueError("AuditSlotKey.recipe_execution_id has the wrong type")
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("AuditSlotKey.installation_version has the wrong type")
        _require_nonempty("AuditSlotKey.step_name", self.step_name)
        _require_digest(
            "AuditSlotKey.invocation_template_digest",
            self.invocation_template_digest,
        )
        _require_digest("AuditSlotKey.slot_intent_digest", self.slot_intent_digest)
        _require_digest(
            "AuditSlotKey.ordered_reference_identity",
            self.ordered_reference_identity,
        )
        _require_optional_digest(
            "AuditSlotKey.prior_authority_digest",
            self.prior_authority_digest,
        )


@dataclass(frozen=True, slots=True)
class AuditIdentityReservation:
    slot_id: AuditSlotId
    slot_key: AuditSlotKey
    current_attempt_id: AuditAttemptId
    runtime_binding_digest: str
    reference_identity_profile_id: str
    audited_plan_refs: tuple[ArtifactRef, ...]
    plan_set_id: str
    cycle_id: str
    scope_id: str
    part_id: str
    audit_round: AuditRound
    parent_authority_digest: str | None
    generated_at: str
    allowed_root: Path
    semantic_result_path: Path
    inventory_path: Path
    authority_path: Path
    expected_head: AuditCycleHead | None

    def __post_init__(self) -> None:
        if not isinstance(self.slot_id, AuditSlotId):
            raise ValueError("AuditIdentityReservation.slot_id has the wrong type")
        if not isinstance(self.slot_key, AuditSlotKey):
            raise ValueError("AuditIdentityReservation.slot_key has the wrong type")
        if not isinstance(self.current_attempt_id, AuditAttemptId):
            raise ValueError("AuditIdentityReservation.current_attempt_id has the wrong type")
        _require_digest(
            "AuditIdentityReservation.runtime_binding_digest",
            self.runtime_binding_digest,
        )
        if self.reference_identity_profile_id != AUDIT_REFERENCE_IDENTITY_PROFILE_V1.profile_id:
            raise ValueError(
                "AuditIdentityReservation.reference_identity_profile_id is unsupported"
            )
        refs = _typed_tuple(
            "AuditIdentityReservation.audited_plan_refs",
            self.audited_plan_refs,
            ArtifactRef,
        )
        if not refs:
            raise ValueError("AuditIdentityReservation.audited_plan_refs must be non-empty")
        reference_identity = compute_audit_reference_identity(refs)
        if reference_identity != self.slot_key.ordered_reference_identity:
            raise ValueError("AuditIdentityReservation ordered reference identity does not match")
        if self.plan_set_id != reference_identity:
            raise ValueError(
                "AuditIdentityReservation.plan_set_id must be the ordered reference identity"
            )
        for field_name in ("plan_set_id", "cycle_id", "scope_id", "part_id", "generated_at"):
            _require_nonempty(
                f"AuditIdentityReservation.{field_name}",
                getattr(self, field_name),
            )
        if not isinstance(self.audit_round, AuditRound):
            raise ValueError("AuditIdentityReservation.audit_round has the wrong type")
        _require_optional_digest(
            "AuditIdentityReservation.parent_authority_digest",
            self.parent_authority_digest,
        )
        if self.parent_authority_digest != self.slot_key.prior_authority_digest:
            raise ValueError("AuditIdentityReservation parent digest does not match slot key")
        root = _require_absolute_path("AuditIdentityReservation.allowed_root", self.allowed_root)
        artifact_paths = (
            self.semantic_result_path,
            self.inventory_path,
            self.authority_path,
        )
        for field_name, path in zip(
            ("semantic_result_path", "inventory_path", "authority_path"),
            artifact_paths,
            strict=True,
        ):
            checked_path = _require_absolute_path(f"AuditIdentityReservation.{field_name}", path)
            try:
                checked_path.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"AuditIdentityReservation.{field_name} must be under allowed_root"
                ) from exc
        if len(set(artifact_paths)) != len(artifact_paths):
            raise ValueError("AuditIdentityReservation artifact paths must be distinct")
        if self.expected_head is not None:
            if not isinstance(self.expected_head, AuditCycleHead):
                raise ValueError("AuditIdentityReservation.expected_head has the wrong type")
            if self.parent_authority_digest != self.expected_head.current_authority_digest:
                raise ValueError(
                    "AuditIdentityReservation expected head digest does not match parent"
                )


@dataclass(frozen=True, slots=True)
class AuditPreparedEffect:
    artifact_kind: str
    canonical_bytes: bytes
    content_digest: str
    path: Path
    delivery_status: AuditPreparedEffectDeliveryStatus
    canonicalization_profile: str
    semantic_fingerprint: str

    def __post_init__(self) -> None:
        _require_nonempty("AuditPreparedEffect.artifact_kind", self.artifact_kind)
        if not isinstance(self.canonical_bytes, bytes):
            raise ValueError("AuditPreparedEffect.canonical_bytes must be bytes")
        _require_digest("AuditPreparedEffect.content_digest", self.content_digest)
        if compute_bytes_hash(self.canonical_bytes) != self.content_digest:
            raise ValueError("AuditPreparedEffect.content_digest does not match canonical_bytes")
        _require_absolute_path("AuditPreparedEffect.path", self.path)
        if not isinstance(self.delivery_status, AuditPreparedEffectDeliveryStatus):
            raise ValueError("AuditPreparedEffect.delivery_status has the wrong type")
        _require_nonempty(
            "AuditPreparedEffect.canonicalization_profile",
            self.canonicalization_profile,
        )
        _require_digest(
            "AuditPreparedEffect.semantic_fingerprint",
            self.semantic_fingerprint,
        )

    @property
    def byte_size(self) -> int:
        return len(self.canonical_bytes)


def _validate_result_payload(
    *,
    owner: str,
    successful: bool,
    verdict: object,
    path: object,
    error: object,
) -> None:
    if successful:
        if not isinstance(verdict, AuditVerdict):
            raise ValueError(f"{owner}.verdict is required for a successful status")
        _require_absolute_path(f"{owner}.path", path)
        if error is not None:
            raise ValueError(f"{owner}.error must be None for a successful status")
        return
    if verdict is not None:
        raise ValueError(f"{owner}.verdict must be None for a failure status")
    if path is not None:
        raise ValueError(f"{owner}.path must be None for a failure status")
    _require_nonempty(f"{owner}.error", error)


def _validate_standalone_payload(
    *,
    owner: str,
    verdict: object,
    path: object,
    error: object,
) -> None:
    if verdict is not None or path is not None or error is not None:
        raise ValueError(f"{owner} NON_PUBLISHED_STANDALONE cannot expose authority payload")


@dataclass(frozen=True, slots=True)
class AuditMaterializationResult:
    status: AuditMaterializationStatus
    attempt_id: AuditAttemptId
    verdict: AuditVerdict | None
    path: Path | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuditMaterializationStatus):
            raise ValueError("AuditMaterializationResult.status has the wrong type")
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditMaterializationResult.attempt_id has the wrong type")
        if self.status is AuditMaterializationStatus.NON_PUBLISHED_STANDALONE:
            _validate_standalone_payload(
                owner="AuditMaterializationResult",
                verdict=self.verdict,
                path=self.path,
                error=self.error,
            )
            return
        _validate_result_payload(
            owner="AuditMaterializationResult",
            successful=self.status
            in {
                AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION,
                AuditMaterializationStatus.EXACT_REPLAY,
            },
            verdict=self.verdict,
            path=self.path,
            error=self.error,
        )


@dataclass(frozen=True, slots=True)
class AuditOutcome:
    status: AuditOutcomeStatus
    attempt_id: AuditAttemptId
    verdict: AuditVerdict | None
    path: Path | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuditOutcomeStatus):
            raise ValueError("AuditOutcome.status has the wrong type")
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditOutcome.attempt_id has the wrong type")
        if self.status is AuditOutcomeStatus.NON_PUBLISHED_STANDALONE:
            _validate_standalone_payload(
                owner="AuditOutcome",
                verdict=self.verdict,
                path=self.path,
                error=self.error,
            )
            return
        _validate_result_payload(
            owner="AuditOutcome",
            successful=self.status
            in {
                AuditOutcomeStatus.PUBLISHED,
                AuditOutcomeStatus.EXACT_REPLAY,
            },
            verdict=self.verdict,
            path=self.path,
            error=self.error,
        )


@dataclass(frozen=True, slots=True)
class AuditAttemptRecord:
    slot_id: AuditSlotId
    attempt_id: AuditAttemptId
    lifecycle: AuditAttemptLifecycle
    semantic_digest: str | None
    correction_predecessor: AuditAttemptId | None
    prepared_effects: tuple[AuditPreparedEffect, ...]
    committed_outcome: AuditOutcome | None

    def __post_init__(self) -> None:
        if not isinstance(self.slot_id, AuditSlotId):
            raise ValueError("AuditAttemptRecord.slot_id has the wrong type")
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditAttemptRecord.attempt_id has the wrong type")
        if not isinstance(self.lifecycle, AuditAttemptLifecycle):
            raise ValueError("AuditAttemptRecord.lifecycle has the wrong type")
        _require_optional_digest("AuditAttemptRecord.semantic_digest", self.semantic_digest)
        if self.correction_predecessor is not None:
            if not isinstance(self.correction_predecessor, AuditAttemptId):
                raise ValueError("AuditAttemptRecord.correction_predecessor has the wrong type")
            if self.correction_predecessor == self.attempt_id:
                raise ValueError("AuditAttemptRecord cannot correct its own attempt")
        effects = _typed_tuple(
            "AuditAttemptRecord.prepared_effects",
            self.prepared_effects,
            AuditPreparedEffect,
        )
        if len({effect.path for effect in effects}) != len(effects):
            raise ValueError("AuditAttemptRecord.prepared_effects contain duplicate paths")
        if effects and self.semantic_digest is None:
            raise ValueError("prepared effects require semantic_digest")
        if self.lifecycle is AuditAttemptLifecycle.OPEN and (
            self.semantic_digest is not None or effects or self.committed_outcome is not None
        ):
            raise ValueError("OPEN attempt cannot carry processed state")
        if (
            self.lifecycle
            in {
                AuditAttemptLifecycle.SEMANTIC_ACCEPTED,
                AuditAttemptLifecycle.SEMANTIC_REJECTED,
            }
            and self.semantic_digest is None
        ):
            raise ValueError(f"{self.lifecycle.value} attempt requires semantic_digest")
        if (
            self.lifecycle
            in {
                AuditAttemptLifecycle.SEMANTIC_ACCEPTED,
                AuditAttemptLifecycle.SEMANTIC_REJECTED,
            }
            and effects
        ):
            raise ValueError("semantic-stage attempt cannot carry prepared effects")
        if (
            self.lifecycle
            in {
                AuditAttemptLifecycle.PREPARED,
                AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION,
            }
            and not effects
        ):
            raise ValueError(f"{self.lifecycle.value} attempt requires prepared effects")
        if (self.lifecycle is AuditAttemptLifecycle.RESPONSE_COMMITTED) != (
            self.committed_outcome is not None
        ):
            raise ValueError("committed_outcome is present exactly for RESPONSE_COMMITTED")
        if (
            self.committed_outcome is not None
            and self.committed_outcome.attempt_id != self.attempt_id
        ):
            raise ValueError("AuditAttemptRecord committed outcome belongs to another attempt")

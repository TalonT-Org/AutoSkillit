"""Audit reference-identity definitions and calculation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..closure_hashing import canonical_json_bytes, compute_canonical_hash
from ._type_audit_admission_validation import _require_nonempty, _typed_tuple
from ._type_audit_cycle_authority import ArtifactRef

_REFERENCE_IDENTITY_DOMAIN = "autoskillit:audit-admission:ordered-full-reference:v1:sha256"
_FULL_REFERENCE_FIELDS = (
    "locator",
    "media_type",
    "schema_version",
    "byte_size",
    "content_digest",
)


def _full_reference_key(reference: ArtifactRef) -> tuple[object, ...]:
    payload = reference.to_dict()
    return tuple(payload[field_name] for field_name in _FULL_REFERENCE_FIELDS)


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

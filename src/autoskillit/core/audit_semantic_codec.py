"""Strict bounded loader for child-produced audit semantics.

The semantic artifact intentionally excludes lifecycle, lineage, installation,
and output-location authority.  Those values belong to a parent-owned
materialization step, not to this codec.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from .io import decode_versioned_json_bytes
from .path_containment import ContainmentError, read_stable_contained_bytes
from .types._type_audit_admission import (
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    STANDALONE_AUDIT_EVIDENCE_KIND,
    STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    AuditSemanticResult,
    StandaloneAuditEvidence,
)
from .types._type_audit_cycle import ArtifactRef

__all__ = [
    "AuditSemanticCodecError",
    "canonical_full_reference_records_match",
    "load_audit_semantic_result",
    "load_standalone_audit_evidence",
]

_DEFAULT_MAX_SIZE_BYTES = 10_000_000
_TOP_LEVEL_KEYS = frozenset(
    {
        "assessments",
        "audited_plan_refs",
        "remediation_ref",
        "schema_version",
        "verdict",
    }
)
_STANDALONE_TOP_LEVEL_KEYS = _TOP_LEVEL_KEYS | {"kind"}
_ARTIFACT_REF_KEYS = frozenset(
    {
        "byte_size",
        "content_digest",
        "locator",
        "media_type",
        "schema_version",
    }
)
_ASSESSMENT_KEYS = frozenset(
    {
        "assessment",
        "evidence_summary",
        "requirement_id",
        "requirement_text",
        "row_digest",
    }
)

AuditSemanticCodecReason = Literal[
    "artifact_read_failed",
    "invalid_canonical_json",
    "invalid_semantic_schema",
    "forbidden_identity_field",
    "prepared_effect_mismatch",
]


class _ArtifactByteReader(Protocol):
    def __call__(
        self,
        path: str | Path,
        allowed_root: str | Path,
        /,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]: ...


class AuditSemanticCodecError(ValueError):
    """Fail-closed semantic-artifact rejection with a stable machine reason."""

    def __init__(self, reason: AuditSemanticCodecReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _require_exact_mapping(
    value: object,
    *,
    expected_keys: frozenset[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be an object",
        )
    actual_keys = frozenset(value)
    unknown = actual_keys - expected_keys
    if unknown:
        rendered = ", ".join(sorted(repr(key) for key in unknown))
        raise AuditSemanticCodecError(
            "forbidden_identity_field",
            f"{path} contains forbidden field(s): {rendered}",
        )
    missing = expected_keys - actual_keys
    if missing:
        rendered = ", ".join(sorted(repr(key) for key in missing))
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} is missing required field(s): {rendered}",
        )
    return value


def _require_exact_sequence(value: object, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be an array",
        )
    return value


def _require_scalar(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        rendered = ", ".join(sorted(repr(key) for key in value))
        raise AuditSemanticCodecError(
            "forbidden_identity_field",
            f"{path} contains forbidden nested field(s): {rendered}",
        )
    if isinstance(value, list):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be a scalar",
        )


def _validate_exact_recursive_schema(raw: dict[str, Any]) -> None:
    top = _require_exact_mapping(raw, expected_keys=_TOP_LEVEL_KEYS, path="$")
    if (
        isinstance(top["schema_version"], bool)
        or not isinstance(top["schema_version"], int)
        or top["schema_version"] != AUDIT_SEMANTIC_SCHEMA_VERSION
    ):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "$.schema_version is invalid",
        )

    audited_plan_refs = _require_exact_sequence(
        top["audited_plan_refs"],
        path="$.audited_plan_refs",
    )
    for index, ref in enumerate(audited_plan_refs):
        ref_mapping = _require_exact_mapping(
            ref,
            expected_keys=_ARTIFACT_REF_KEYS,
            path=f"$.audited_plan_refs[{index}]",
        )
        for field, value in ref_mapping.items():
            _require_scalar(
                value,
                path=f"$.audited_plan_refs[{index}].{field}",
            )

    assessments = _require_exact_sequence(top["assessments"], path="$.assessments")
    for index, assessment in enumerate(assessments):
        assessment_mapping = _require_exact_mapping(
            assessment,
            expected_keys=_ASSESSMENT_KEYS,
            path=f"$.assessments[{index}]",
        )
        for field, value in assessment_mapping.items():
            _require_scalar(
                value,
                path=f"$.assessments[{index}].{field}",
            )

    remediation_ref = top["remediation_ref"]
    if remediation_ref is not None:
        remediation_mapping = _require_exact_mapping(
            remediation_ref,
            expected_keys=_ARTIFACT_REF_KEYS,
            path="$.remediation_ref",
        )
        for field, value in remediation_mapping.items():
            _require_scalar(value, path=f"$.remediation_ref.{field}")
    _require_scalar(top["verdict"], path="$.verdict")


def canonical_full_reference_records_match(
    left: Sequence[ArtifactRef],
    right: Sequence[ArtifactRef],
) -> bool:
    """Compare ordered references using every canonical field.

    ``ArtifactRef`` equality intentionally compares only its content digest, so
    it is not a sufficient authority-boundary comparison.
    """

    return tuple(ref.to_dict() for ref in left) == tuple(ref.to_dict() for ref in right)


def load_audit_semantic_result(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    reader: _ArtifactByteReader = read_stable_contained_bytes,
) -> AuditSemanticResult:
    """Load one strict canonical audit-semantic artifact beneath ``allowed_root``."""

    try:
        _, data = reader(path, allowed_root, max_size_bytes=max_size_bytes)
    except (ContainmentError, OSError) as exc:
        raise AuditSemanticCodecError(
            "artifact_read_failed",
            f"audit semantic artifact containment/read failed: {exc}",
        ) from exc

    raw = decode_versioned_json_bytes(
        data,
        expected_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
        require_canonical=True,
    )
    if raw is None:
        raise AuditSemanticCodecError(
            "invalid_canonical_json",
            "audit semantic artifact is not strict canonical versioned JSON",
        )

    _validate_exact_recursive_schema(raw)
    try:
        result = AuditSemanticResult.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"audit semantic artifact validation failed: {exc}",
        ) from exc
    if result.to_dict() != raw:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "audit semantic artifact does not round-trip exactly",
        )
    return result


def load_standalone_audit_evidence(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    reader: _ArtifactByteReader = read_stable_contained_bytes,
) -> StandaloneAuditEvidence:
    """Load one strict canonical standalone-audit artifact beneath ``allowed_root``."""

    try:
        _, data = reader(path, allowed_root, max_size_bytes=max_size_bytes)
    except (ContainmentError, OSError) as exc:
        raise AuditSemanticCodecError(
            "artifact_read_failed",
            f"standalone audit artifact containment/read failed: {exc}",
        ) from exc

    raw = decode_versioned_json_bytes(
        data,
        expected_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
        require_canonical=True,
    )
    if raw is None or set(raw) != _STANDALONE_TOP_LEVEL_KEYS:
        raise AuditSemanticCodecError(
            "invalid_canonical_json",
            "standalone audit artifact is not strict canonical versioned JSON",
        )
    if raw["kind"] != STANDALONE_AUDIT_EVIDENCE_KIND:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "standalone audit artifact has an invalid kind",
        )

    _validate_exact_recursive_schema({key: value for key, value in raw.items() if key != "kind"})
    try:
        result = StandaloneAuditEvidence.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"standalone audit artifact validation failed: {exc}",
        ) from exc
    if result.to_dict() != raw:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "standalone audit artifact does not round-trip exactly",
        )
    return result

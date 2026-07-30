"""Strict framed-ledger primitives for shell-capture lifecycle state."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import struct
from dataclasses import dataclass, replace
from typing import Any, cast

from . import _snapshot
from ._lifecycle_policy import (
    CaptureDeliveryStatus,
    CaptureReferenceStatus,
    CaptureRetentionPhase,
    CaptureSnapshotStatus,
    CaptureState,
    CaptureStatus,
    is_delivery_successor,
    is_reference_successor,
    is_retention_successor,
    is_state_successor,
)
from ._module_identity import register_module_aliases
from ._snapshot import (
    CaptureFinalManifest,
)
from ._syntax import (
    CAPTURE_ID_RE as _CAPTURE_ID_RE,
)
from ._syntax import (
    INCARNATION_RE as _INCARNATION_RE,
)
from ._types import CaptureFailureEvidence, LegacyCleanupOnly

register_module_aliases(__name__)

__all__ = [
    "CURRENT_FORMAT_VERSION",
    "FRAME_MAGIC",
    "MAX_FRAME_BYTES",
    "MAX_LEDGER_BYTES",
    "CaptureDeliveryStatus",
    "CaptureLifecycleRecord",
    "CaptureReferenceStatus",
    "CaptureRetentionPhase",
    "CaptureSnapshotStatus",
    "CaptureStatus",
    "CaptureState",
    "CaptureTransitionCommittedError",
    "DecodedLedger",
    "LedgerCodecError",
    "LedgerFrame",
    "canonical_json",
    "decode_ledger",
    "encode_frame",
    "legacy_record_from_dict",
    "record_from_dict",
    "record_to_dict",
    "same_record",
    "is_delivery_successor",
    "is_reference_successor",
    "is_retention_successor",
    "is_state_successor",
    "validate_successor",
    "validate_record",
    "write_all",
]

FRAME_MAGIC = b"ASCL"
CURRENT_FORMAT_VERSION = 2
MAX_FRAME_BYTES = 64 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
_FRAME_HEADER_FORMAT = ">4sI"
_FRAME_HEADER_BYTES = 8
_CHECKSUM_BYTES = hashlib.sha256().digest_size
_MAX_NESTING = 16
_MAX_JSON_NODES = 4096


class LedgerCodecError(RuntimeError):
    """Raised when framed lifecycle bytes are not strictly recoverable."""


class CaptureTransitionCommittedError(RuntimeError):
    """Raised only after a lifecycle transition frame was durably synced."""


_PUBLIC_NAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")
_STAGING_NAME_RE = re.compile(r"^\.capture-staging-[0-9a-f]{16}-[0-9a-f]{16}$")
_QUARANTINE_NAME_RE = re.compile(r"^\.capture-quarantine-[0-9a-f]{16}-[0-9a-f]{16}$")


@dataclass(frozen=True, slots=True)
class CaptureLifecycleRecord:
    capture_id: str
    state: CaptureState
    staging_name: str
    public_name: str
    project_identity: tuple[int, int]
    root_identity: tuple[int, int]
    created_at: float
    next_attempt_at: float
    incarnation: str = ""
    revision: int = 0
    finalized_at_revision: int | None = None
    compaction_epoch: int = 1
    artifact_identity: tuple[int, int] | None = None
    retention_at: float | None = None
    observed_size: int = 0
    manifest: CaptureFinalManifest | None = None
    manifest_bytes: bytes = b""
    failure: CaptureFailureEvidence | None = None
    legacy_cleanup: LegacyCleanupOnly | None = None
    capture_status: CaptureStatus = CaptureStatus.PENDING
    snapshot_status: CaptureSnapshotStatus = CaptureSnapshotStatus.ABSENT
    reference_status: CaptureReferenceStatus = CaptureReferenceStatus.NOT_REQUESTED
    delivery_status: CaptureDeliveryStatus = CaptureDeliveryStatus.NOT_ATTEMPTED
    retention_phase: CaptureRetentionPhase = CaptureRetentionPhase.ACTIVE
    retry_count: int = 0
    deletion_nonce: str = ""
    quarantine_name: str = ""

    def __post_init__(self) -> None:
        validate_record(self)

    @property
    def size(self) -> int:
        if self.manifest is not None:
            return self.manifest.total_bytes
        return self.observed_size

    @property
    def sha256(self) -> str:
        return self.manifest.sha256 if self.manifest is not None else ""


def same_record(
    expected: CaptureLifecycleRecord,
    current: CaptureLifecycleRecord | None,
) -> bool:
    """Compare one logical record while ignoring its load-time compaction epoch."""

    return (
        current is not None
        and replace(
            current,
            compaction_epoch=expected.compaction_epoch,
        )
        == expected
    )


def _plain_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _pair(value: object, field: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not _plain_int(part) for part in value)
    ):
        raise LedgerCodecError(f"invalid {field}")
    return (value[0], value[1])


def _finite_timestamp(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise LedgerCodecError(f"invalid {field}")
    return float(value)


def _failure_from_dict(value: object) -> CaptureFailureEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "detail",
        "settlement_returncode",
        "stage",
    }:
        raise LedgerCodecError("invalid capture failure evidence")
    try:
        return CaptureFailureEvidence(
            stage=value["stage"],
            detail=value["detail"],
            settlement_returncode=value["settlement_returncode"],
        )
    except (RuntimeError, TypeError) as exc:
        raise LedgerCodecError("invalid capture failure evidence") from exc


def record_to_dict(record: CaptureLifecycleRecord) -> dict[str, object]:
    validate_record(record)
    return {
        "artifact_identity": (
            list(record.artifact_identity) if record.artifact_identity is not None else None
        ),
        "capture_id": record.capture_id,
        "capture_status": record.capture_status.value,
        "created_at": record.created_at,
        "deletion_nonce": record.deletion_nonce,
        "delivery_status": record.delivery_status.value,
        "failure": (
            {
                "detail": record.failure.detail,
                "settlement_returncode": record.failure.settlement_returncode,
                "stage": record.failure.stage,
            }
            if record.failure is not None
            else None
        ),
        "finalized_at_revision": record.finalized_at_revision,
        "incarnation": record.incarnation,
        "legacy_observed_size": (
            record.legacy_cleanup.observed_size if record.legacy_cleanup is not None else None
        ),
        "manifest_b64": (
            base64.b64encode(record.manifest_bytes).decode("ascii")
            if record.manifest_bytes
            else ""
        ),
        "next_attempt_at": record.next_attempt_at,
        "observed_size": record.observed_size,
        "project_identity": list(record.project_identity),
        "public_name": record.public_name,
        "quarantine_name": record.quarantine_name,
        "reference_status": record.reference_status.value,
        "retention_at": record.retention_at,
        "retention_phase": record.retention_phase.value,
        "retry_count": record.retry_count,
        "revision": record.revision,
        "root_identity": list(record.root_identity),
        "staging_name": record.staging_name,
        "state": record.state.value,
        "snapshot_status": record.snapshot_status.value,
    }


def record_from_dict(value: object) -> CaptureLifecycleRecord:
    if not isinstance(value, dict):
        raise LedgerCodecError("record is not an object")
    expected_fields = {
        "artifact_identity",
        "capture_id",
        "capture_status",
        "created_at",
        "deletion_nonce",
        "delivery_status",
        "failure",
        "finalized_at_revision",
        "incarnation",
        "legacy_observed_size",
        "manifest_b64",
        "next_attempt_at",
        "observed_size",
        "project_identity",
        "public_name",
        "quarantine_name",
        "reference_status",
        "retention_at",
        "retention_phase",
        "retry_count",
        "revision",
        "root_identity",
        "staging_name",
        "state",
        "snapshot_status",
    }
    if set(value) != expected_fields:
        raise LedgerCodecError("lifecycle record fields do not match schema")
    try:
        record_state = CaptureState(value["state"])
        capture_status = CaptureStatus(value["capture_status"])
        snapshot_status = CaptureSnapshotStatus(value["snapshot_status"])
        reference_status = CaptureReferenceStatus(value["reference_status"])
        delivery_status = CaptureDeliveryStatus(value["delivery_status"])
        retention_phase = CaptureRetentionPhase(value["retention_phase"])
        manifest_b64 = value["manifest_b64"]
        manifest_bytes = base64.b64decode(manifest_b64, validate=True) if manifest_b64 else b""
        manifest = (
            _snapshot._restore_capture_final_manifest(
                _snapshot.decode_capture_manifest_wire(manifest_bytes)
            )
            if manifest_bytes
            else None
        )
        legacy_size = value["legacy_observed_size"]
        record = CaptureLifecycleRecord(
            capture_id=value["capture_id"],
            state=record_state,
            staging_name=value["staging_name"],
            public_name=value["public_name"],
            project_identity=_pair(value["project_identity"], "project identity"),
            root_identity=_pair(value["root_identity"], "root identity"),
            created_at=_finite_timestamp(value["created_at"], "created timestamp"),
            next_attempt_at=_finite_timestamp(value["next_attempt_at"], "next-attempt timestamp"),
            incarnation=value["incarnation"],
            revision=value["revision"],
            finalized_at_revision=value["finalized_at_revision"],
            artifact_identity=(
                None
                if value["artifact_identity"] is None
                else _pair(value["artifact_identity"], "artifact identity")
            ),
            retention_at=(
                None
                if value["retention_at"] is None
                else _finite_timestamp(value["retention_at"], "retention timestamp")
            ),
            observed_size=value["observed_size"],
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            failure=_failure_from_dict(value["failure"]),
            legacy_cleanup=(None if legacy_size is None else LegacyCleanupOnly(legacy_size)),
            capture_status=capture_status,
            snapshot_status=snapshot_status,
            reference_status=reference_status,
            delivery_status=delivery_status,
            retention_phase=retention_phase,
            retry_count=value["retry_count"],
            deletion_nonce=value["deletion_nonce"],
            quarantine_name=value["quarantine_name"],
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise LedgerCodecError("invalid lifecycle record fields") from exc
    validate_record(record)
    return record


def validate_record(record: CaptureLifecycleRecord) -> None:
    if (
        type(record) is not CaptureLifecycleRecord
        or not _CAPTURE_ID_RE.fullmatch(record.capture_id)
        or not _STAGING_NAME_RE.fullmatch(record.staging_name)
        or not _PUBLIC_NAME_RE.fullmatch(record.public_name)
        or not _INCARNATION_RE.fullmatch(record.incarnation)
        or not _plain_int(record.revision, minimum=1)
        or not _plain_int(record.compaction_epoch, minimum=1)
        or not _plain_int(record.observed_size)
        or not _plain_int(record.retry_count)
        or not isinstance(record.deletion_nonce, str)
        or not isinstance(record.quarantine_name, str)
        or (record.quarantine_name and not _QUARANTINE_NAME_RE.fullmatch(record.quarantine_name))
    ):
        raise LedgerCodecError("invalid lifecycle record fields")
    _finite_timestamp(record.created_at, "created timestamp")
    _finite_timestamp(record.next_attempt_at, "next-attempt timestamp")
    if record.retention_at is not None:
        _finite_timestamp(record.retention_at, "retention timestamp")
    for field_name, identity in (
        ("project identity", record.project_identity),
        ("root identity", record.root_identity),
    ):
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or any(not _plain_int(part) for part in identity)
        ):
            raise LedgerCodecError(f"invalid {field_name}")
    if record.artifact_identity is not None and (
        not isinstance(record.artifact_identity, tuple)
        or len(record.artifact_identity) != 2
        or any(not _plain_int(part) for part in record.artifact_identity)
    ):
        raise LedgerCodecError("invalid artifact identity")
    if (record.state is CaptureState.RESERVED and record.artifact_identity is not None) or (
        record.state in {CaptureState.STAGED, CaptureState.PUBLISHED_WRITING}
        and record.artifact_identity is None
    ):
        raise LedgerCodecError("artifact identity does not match lifecycle state")
    if (
        type(record.capture_status) is not CaptureStatus
        or type(record.snapshot_status) is not CaptureSnapshotStatus
    ):
        raise LedgerCodecError("invalid capture outcome axes")
    if record.manifest is None:
        if record.manifest_bytes or record.finalized_at_revision is not None:
            raise LedgerCodecError("manifest fields exist without FINAL authority")
    elif (
        type(record.manifest) is not CaptureFinalManifest
        or not record.manifest_bytes
        or record.finalized_at_revision != record.manifest.finalized_at_revision
        or record.manifest.capture_id != record.capture_id
        or record.manifest.incarnation != record.incarnation
        or record.manifest.project_identity != record.project_identity
        or record.manifest.root_identity != record.root_identity
        or record.manifest.carrier_name != record.public_name
        or record.manifest.carrier_identity != record.artifact_identity
        or record.manifest_bytes != _snapshot.encode_capture_final_manifest(record.manifest)
        or record.state
        not in {
            CaptureState.FINALIZED,
            CaptureState.DELETING,
            CaptureState.DELETED,
            CaptureState.TAMPERED,
        }
    ):
        raise LedgerCodecError("invalid immutable FINAL manifest")
    if (record.manifest is None) != (record.snapshot_status is CaptureSnapshotStatus.ABSENT):
        raise LedgerCodecError("snapshot status does not match immutable authority")
    if (record.manifest is None) != (record.capture_status is not CaptureStatus.COMPLETE):
        raise LedgerCodecError("complete capture outcome does not match FINAL authority")
    if record.manifest is not None and (
        record.capture_status is not CaptureStatus.COMPLETE
        or record.manifest.capture_status is not CaptureStatus.COMPLETE
    ):
        raise LedgerCodecError("FINAL manifest does not carry complete capture status")
    if record.state is CaptureState.FINALIZED and (
        record.capture_status is not CaptureStatus.COMPLETE
        or record.snapshot_status is not CaptureSnapshotStatus.VERIFIED
    ):
        raise LedgerCodecError("FINALIZED state lacks verified complete outcome")
    if record.state in {
        CaptureState.RESERVED,
        CaptureState.STAGED,
        CaptureState.PUBLISHED_WRITING,
    } and (
        record.capture_status is not CaptureStatus.PENDING
        or record.snapshot_status is not CaptureSnapshotStatus.ABSENT
    ):
        raise LedgerCodecError("pending lifecycle state carries terminal outcome")
    if record.state is CaptureState.FAILED and record.failure is None:
        raise LedgerCodecError("FAILED capture lacks failure evidence")
    if record.failure is not None and record.state not in {
        CaptureState.FAILED,
        CaptureState.DELETING,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    }:
        raise LedgerCodecError("failure evidence exists outside FAILED state")
    if (record.failure is None) != (record.capture_status is not CaptureStatus.FAILED):
        raise LedgerCodecError("failed capture outcome is inconsistent")
    if record.legacy_cleanup is not None and record.manifest is not None:
        raise LedgerCodecError("legacy cleanup evidence cannot carry authority")
    if (record.legacy_cleanup is None) != (
        record.capture_status is not CaptureStatus.LEGACY_CLEANUP_ONLY
    ):
        raise LedgerCodecError("legacy cleanup outcome is inconsistent")
    if record.capture_status is CaptureStatus.PENDING and any(
        value is not None for value in (record.manifest, record.failure, record.legacy_cleanup)
    ):
        raise LedgerCodecError("pending capture carries terminal evidence")
    if record.reference_status is CaptureReferenceStatus.NOT_REQUESTED:
        if record.manifest is not None and record.manifest.reference_hash is not None:
            raise LedgerCodecError("issued manifest has not-requested reference state")
    elif record.manifest is None or record.manifest.reference_hash is None:
        raise LedgerCodecError("reference state lacks an issued manifest binding")
    if record.manifest is None and (
        record.delivery_status is not CaptureDeliveryStatus.NOT_ATTEMPTED
        or record.reference_status is not CaptureReferenceStatus.NOT_REQUESTED
    ):
        raise LedgerCodecError("non-FINAL capture carries delivery authority")
    if (
        record.reference_status is CaptureReferenceStatus.ISSUED
        and record.delivery_status is not CaptureDeliveryStatus.NOT_ATTEMPTED
    ):
        raise LedgerCodecError("issued reference cannot have begun delivery")
    if (record.retention_phase is CaptureRetentionPhase.DELETED) != (
        record.state is CaptureState.DELETED
    ):
        raise LedgerCodecError("deleted retention state is inconsistent")
    if (record.retention_phase is CaptureRetentionPhase.TAMPERED) != (
        record.state is CaptureState.TAMPERED
    ):
        raise LedgerCodecError("tampered retention state is inconsistent")
    if (record.retention_phase is CaptureRetentionPhase.DELETING) != (
        record.state is CaptureState.DELETING
    ):
        raise LedgerCodecError("deleting retention state is inconsistent")


def validate_successor(
    previous: CaptureLifecycleRecord,
    candidate: CaptureLifecycleRecord,
) -> None:
    validate_record(previous)
    validate_record(candidate)
    if (
        previous.state is CaptureState.DELETED
        and candidate.state is CaptureState.RESERVED
        and candidate.capture_id == previous.capture_id
        and candidate.incarnation != previous.incarnation
        and candidate.revision == 1
        and candidate.project_identity == previous.project_identity
        and candidate.root_identity == previous.root_identity
        and candidate.public_name == previous.public_name
        and candidate.artifact_identity is None
        and candidate.observed_size == 0
        and candidate.retry_count == 0
        and not candidate.deletion_nonce
        and not candidate.quarantine_name
    ):
        return
    reference_successor = is_reference_successor(
        previous.reference_status,
        candidate.reference_status,
    ) or (
        previous.state is CaptureState.PUBLISHED_WRITING
        and candidate.state is CaptureState.FINALIZED
        and previous.reference_status is CaptureReferenceStatus.NOT_REQUESTED
        and candidate.reference_status
        in {
            CaptureReferenceStatus.NOT_REQUESTED,
            CaptureReferenceStatus.ISSUED,
        }
    )
    mutable_axis_changes = sum(
        (
            candidate.reference_status is not previous.reference_status,
            candidate.delivery_status is not previous.delivery_status,
            candidate.retention_phase is not previous.retention_phase,
        )
    )
    if (
        candidate.capture_id != previous.capture_id
        or candidate.incarnation != previous.incarnation
        or candidate.revision != previous.revision + 1
        or not is_state_successor(previous.state, candidate.state)
        or not reference_successor
        or not is_delivery_successor(
            previous.delivery_status,
            candidate.delivery_status,
        )
        or not is_retention_successor(
            previous.retention_phase,
            candidate.retention_phase,
        )
        or candidate.project_identity != previous.project_identity
        or candidate.root_identity != previous.root_identity
        or candidate.staging_name != previous.staging_name
        or candidate.public_name != previous.public_name
        or candidate.created_at != previous.created_at
        or (candidate.state is previous.state and mutable_axis_changes > 1)
        or (
            candidate.reference_status is not previous.reference_status
            and candidate.reference_status
            in {
                CaptureReferenceStatus.UNAVAILABLE,
                CaptureReferenceStatus.UNKNOWN,
            }
            and previous.delivery_status is not CaptureDeliveryStatus.NOT_ATTEMPTED
        )
    ):
        raise LedgerCodecError("invalid lifecycle successor")
    if candidate.artifact_identity != previous.artifact_identity and not (
        previous.state is CaptureState.RESERVED
        and candidate.state is CaptureState.STAGED
        and previous.artifact_identity is None
        and candidate.artifact_identity is not None
    ):
        raise LedgerCodecError("artifact identity changed across lifecycle successor")
    if previous.deletion_nonce and (
        candidate.deletion_nonce != previous.deletion_nonce
        or candidate.quarantine_name != previous.quarantine_name
    ):
        raise LedgerCodecError("deletion authority changed")
    if previous.manifest is not None and (
        candidate.manifest_bytes != previous.manifest_bytes
        or candidate.manifest != previous.manifest
        or candidate.finalized_at_revision != previous.finalized_at_revision
        or candidate.capture_status is not previous.capture_status
        or candidate.snapshot_status is not previous.snapshot_status
    ):
        raise LedgerCodecError("immutable FINAL authority changed")
    if previous.failure is not None and (
        candidate.failure != previous.failure
        or candidate.capture_status is not previous.capture_status
    ):
        raise LedgerCodecError("failure outcome changed")
    if previous.legacy_cleanup is not None and (
        candidate.legacy_cleanup != previous.legacy_cleanup
        or candidate.capture_status is not previous.capture_status
    ):
        raise LedgerCodecError("legacy cleanup outcome changed")


def legacy_record_from_dict(
    value: object,
    *,
    revision: int,
    compaction_epoch: int,
) -> CaptureLifecycleRecord:
    if not isinstance(value, dict):
        raise LedgerCodecError("legacy record is not an object")
    required = {
        "artifact_identity",
        "capture_id",
        "created_at",
        "deletion_nonce",
        "next_attempt_at",
        "project_identity",
        "public_name",
        "quarantine_name",
        "retention_at",
        "retry_count",
        "root_identity",
        "sha256",
        "size",
        "staging_name",
        "state",
    }
    if not required.issubset(value) or set(value) - required:
        raise LedgerCodecError("legacy lifecycle record fields do not match schema")
    try:
        capture_id = value["capture_id"]
        old_state = CaptureState(value["state"])
        observed_size = value["size"]
        artifact_value = value["artifact_identity"]
        state = (
            old_state
            if old_state in {CaptureState.DELETED, CaptureState.TAMPERED}
            else CaptureState.ABANDONED
        )
        retention_phase = {
            CaptureState.DELETED: CaptureRetentionPhase.DELETED,
            CaptureState.TAMPERED: CaptureRetentionPhase.TAMPERED,
        }.get(state, CaptureRetentionPhase.ELIGIBLE)
        legacy_bytes = canonical_json(value)
        incarnation = hashlib.sha256(
            b"autoskillit:legacy-capture:v1\0" + legacy_bytes
        ).hexdigest()[:32]
        record = CaptureLifecycleRecord(
            capture_id=capture_id,
            state=state,
            staging_name=value["staging_name"],
            public_name=value["public_name"],
            project_identity=_pair(value["project_identity"], "project identity"),
            root_identity=_pair(value["root_identity"], "root identity"),
            created_at=_finite_timestamp(value["created_at"], "created timestamp"),
            next_attempt_at=_finite_timestamp(value["next_attempt_at"], "next-attempt timestamp"),
            incarnation=incarnation,
            revision=revision,
            compaction_epoch=compaction_epoch,
            artifact_identity=(
                None if artifact_value is None else _pair(artifact_value, "artifact identity")
            ),
            retention_at=(
                None
                if value["retention_at"] is None
                else _finite_timestamp(value["retention_at"], "retention timestamp")
            ),
            observed_size=observed_size,
            legacy_cleanup=LegacyCleanupOnly(observed_size),
            capture_status=CaptureStatus.LEGACY_CLEANUP_ONLY,
            snapshot_status=CaptureSnapshotStatus.ABSENT,
            retention_phase=retention_phase,
            retry_count=value["retry_count"],
            deletion_nonce=value["deletion_nonce"],
            quarantine_name=value["quarantine_name"],
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise LedgerCodecError("invalid legacy lifecycle record fields") from exc
    validate_record(record)
    return record


@dataclass(frozen=True, slots=True)
class LedgerFrame:
    format_version: int
    compaction_epoch: int
    record: dict[str, object]
    canonical_payload: bytes


@dataclass(frozen=True, slots=True)
class DecodedLedger:
    frames: tuple[LedgerFrame, ...]
    truncate_at: int | None


class _DuplicateField(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateField(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite constant: {value}")


def _validate_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    seen = 0
    while stack:
        current, depth = stack.pop()
        seen += 1
        if seen > _MAX_JSON_NODES or depth > _MAX_NESTING:
            raise LedgerCodecError("lifecycle frame JSON exceeds structural bound")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise LedgerCodecError("lifecycle frame contains a non-string key")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise LedgerCodecError("lifecycle frame contains an invalid JSON value")


def canonical_json(value: object) -> bytes:
    _validate_shape(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise LedgerCodecError("lifecycle frame is not canonically encodable") from exc


def _decode_json(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateField as exc:
        raise LedgerCodecError("duplicate lifecycle frame field") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise LedgerCodecError("invalid lifecycle frame payload") from exc
    _validate_shape(decoded)
    if canonical_json(decoded) != payload:
        raise LedgerCodecError("noncanonical lifecycle frame payload")
    if not isinstance(decoded, dict):
        raise LedgerCodecError("lifecycle frame payload is not an object")
    return decoded


def encode_frame(
    record: dict[str, object],
    *,
    compaction_epoch: int,
) -> bytes:
    if (
        not isinstance(compaction_epoch, int)
        or isinstance(compaction_epoch, bool)
        or compaction_epoch < 1
        or not isinstance(record, dict)
    ):
        raise LedgerCodecError("invalid lifecycle frame metadata")
    payload = canonical_json(
        {
            "compaction_epoch": compaction_epoch,
            "format_version": CURRENT_FORMAT_VERSION,
            "record": record,
        }
    )
    if len(payload) > MAX_FRAME_BYTES:
        raise LedgerCodecError("lifecycle frame exceeds bound")
    return (
        struct.pack(_FRAME_HEADER_FORMAT, FRAME_MAGIC, len(payload))
        + payload
        + hashlib.sha256(payload).digest()
    )


def decode_ledger(data: bytes) -> DecodedLedger:
    if not isinstance(data, bytes) or len(data) > MAX_LEDGER_BYTES:
        raise LedgerCodecError("lifecycle ledger exceeds bound")
    frames: list[LedgerFrame] = []
    cursor = 0
    while cursor < len(data):
        remaining = len(data) - cursor
        if remaining < _FRAME_HEADER_BYTES:
            return DecodedLedger(tuple(frames), cursor)
        magic, declared = struct.unpack_from(_FRAME_HEADER_FORMAT, data, cursor)
        if magic != FRAME_MAGIC:
            raise LedgerCodecError("invalid lifecycle frame magic")
        if declared > MAX_FRAME_BYTES:
            raise LedgerCodecError("declared lifecycle frame exceeds bound")
        frame_end = cursor + _FRAME_HEADER_BYTES + declared + _CHECKSUM_BYTES
        if frame_end > len(data):
            return DecodedLedger(tuple(frames), cursor)
        payload_start = cursor + _FRAME_HEADER_BYTES
        payload = data[payload_start : payload_start + declared]
        checksum = data[payload_start + declared : frame_end]
        if not hashlib.sha256(payload).digest() == checksum:
            raise LedgerCodecError("lifecycle frame checksum mismatch")
        decoded = _decode_json(payload)
        if set(decoded) == {"format_version", "generation", "record"}:
            version = decoded["format_version"]
            epoch = decoded["generation"]
        elif set(decoded) == {
            "compaction_epoch",
            "format_version",
            "record",
        }:
            version = decoded["format_version"]
            epoch = decoded["compaction_epoch"]
        else:
            raise LedgerCodecError("lifecycle frame fields do not match a schema")
        if (
            version not in (1, CURRENT_FORMAT_VERSION)
            or not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or epoch < 1
            or not isinstance(decoded["record"], dict)
        ):
            raise LedgerCodecError("invalid lifecycle frame metadata")
        if version == 1 and "generation" not in decoded:
            raise LedgerCodecError("invalid legacy lifecycle frame")
        if version == CURRENT_FORMAT_VERSION and "compaction_epoch" not in decoded:
            raise LedgerCodecError("invalid current lifecycle frame")
        frames.append(
            LedgerFrame(
                format_version=cast(int, version),
                compaction_epoch=epoch,
                record=decoded["record"],
                canonical_payload=payload,
            )
        )
        cursor = frame_end
    return DecodedLedger(tuple(frames), None)


def write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0 or written > len(view) - offset:
            raise LedgerCodecError("lifecycle ledger write made no progress")
        offset += written

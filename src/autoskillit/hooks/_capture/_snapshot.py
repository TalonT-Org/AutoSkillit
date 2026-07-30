"""Factory-only descriptor authority for finalized shell captures."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
from dataclasses import InitVar, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, NoReturn, SupportsIndex

from . import _descriptor, _lifecycle_policy, _syntax
from ._module_identity import register_module_aliases

if TYPE_CHECKING:
    from autoskillit.hooks._capture_contract import (
        CaptureV2Fields,
        CaptureV2Renderable,
        capture_v2_fields,
    )
elif __package__ == "_capture":
    from _capture_contract import CaptureV2Fields, CaptureV2Renderable, capture_v2_fields
else:
    from .._capture_contract import CaptureV2Fields, CaptureV2Renderable, capture_v2_fields

register_module_aliases(__name__)

__all__ = [
    "CaptureAuthorityError",
    "CaptureFinalManifest",
    "CaptureManifestWire",
    "CaptureMeasurement",
    "CaptureReferenceHint",
    "CaptureWriteAuthority",
    "CommandOutcome",
    "CommandOutcomeKind",
    "FinalizedCapture",
    "IssuedCaptureReference",
    "PublishedCaptureReference",
    "UnavailableCaptureReference",
    "VerifiedCaptureSnapshot",
    "decode_capture_manifest_wire",
    "encode_capture_final_manifest",
    "parse_capture_reference",
    "verify_capture_descriptor",
    "verify_capture_snapshot",
]

SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOT_PRODUCER = "codex_shell_capture"
MANAGED_STREAM_DOMAIN = "combined_stdout_stderr_pipe_eof"
MAX_MANIFEST_BYTES = 8 * 1024
MAX_REFERENCE_TOKEN_BYTES = 192

_AUTHORITY_FACTORY_TOKEN = object()
_CARRIER_NAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")
_UNTRUSTED_MODE_BITS = stat.S_IRWXG | stat.S_IRWXO
_READ_CHUNK_BYTES = 64 * 1024
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "producer",
        "capture_id",
        "incarnation",
        "finalized_at_revision",
        "project_identity",
        "root_identity",
        "carrier_name",
        "carrier_identity",
        "stream_domain",
        "total_bytes",
        "sha256",
        "inline_length",
        "head_length",
        "tail_length",
        "capture_status",
        "command_outcome_kind",
        "command_outcome_value",
        "finalized_at",
        "reference_hash",
        "reference_expiry",
        "retention_deadline",
    }
)


CaptureAuthorityError = _descriptor.CaptureAuthorityError
_canonical_json = _descriptor.canonical_json


class _NoAuthorityCopy:
    def __copy__(self) -> NoReturn:
        raise CaptureAuthorityError("capture authority cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        self.__copy__()

    def __reduce__(self) -> NoReturn:
        raise CaptureAuthorityError("capture authority cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        self.__reduce__()


def _require_factory(value: object | None, type_name: str) -> None:
    if value is not _AUTHORITY_FACTORY_TOKEN:
        raise CaptureAuthorityError(f"{type_name} must be factory-created")


def _is_plain_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _identity(
    value: object,
    field: str,
    *,
    wire: bool = False,
) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or isinstance(value, list) != wire
        or len(value) != 2
        or any(not _is_plain_int(part) for part in value)
    ):
        raise CaptureAuthorityError(f"invalid {field}")
    return (value[0], value[1])


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise CaptureAuthorityError(f"invalid {field}")
    return float(value)


class CommandOutcomeKind(StrEnum):
    EXITED = "exited"
    SIGNALED = "signaled"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    kind: CommandOutcomeKind
    value: int

    def __post_init__(self) -> None:
        if type(self.kind) is not CommandOutcomeKind:
            raise CaptureAuthorityError("invalid command outcome kind")
        maximum = 255 if self.kind is CommandOutcomeKind.EXITED else 127
        minimum = 0 if self.kind is CommandOutcomeKind.EXITED else 1
        if not _is_plain_int(self.value, minimum=minimum) or self.value > maximum:
            raise CaptureAuthorityError("invalid command outcome value")

    @classmethod
    def exited(cls, exit_code: int) -> CommandOutcome:
        return cls(CommandOutcomeKind.EXITED, exit_code)

    @classmethod
    def signaled(cls, signal_number: int) -> CommandOutcome:
        return cls(CommandOutcomeKind.SIGNALED, signal_number)

    @classmethod
    def from_wait_result(cls, returncode: int) -> CommandOutcome:
        if not isinstance(returncode, int) or isinstance(returncode, bool):
            raise CaptureAuthorityError("invalid process wait result")
        return cls.signaled(-returncode) if returncode < 0 else cls.exited(returncode)

    @property
    def raw_wait_result(self) -> int:
        return -self.value if self.kind is CommandOutcomeKind.SIGNALED else self.value

    @property
    def shell_returncode(self) -> int:
        return 128 + self.value if self.kind is CommandOutcomeKind.SIGNALED else self.value


@dataclass(frozen=True, slots=True)
class CaptureMeasurement:
    total_bytes: int
    sha256: str
    inline_bytes: int
    inline: bytes
    head: bytes
    tail: bytes

    def __post_init__(self) -> None:
        if not _is_plain_int(self.total_bytes):
            raise CaptureAuthorityError("invalid capture measurement size")
        if not isinstance(self.sha256, str) or not _syntax.SHA256_RE.fullmatch(self.sha256):
            raise CaptureAuthorityError("invalid capture measurement digest")
        if not _is_plain_int(self.inline_bytes, minimum=1):
            raise CaptureAuthorityError("invalid capture measurement inline bound")
        head_limit = (2 * self.inline_bytes) // 3
        tail_limit = self.inline_bytes - head_limit
        expected_lengths = {
            "inline": min(self.total_bytes, self.inline_bytes + 1),
            "head": min(self.total_bytes, head_limit),
            "tail": min(self.total_bytes, tail_limit),
        }
        for field_name in ("inline", "head", "tail"):
            value = getattr(self, field_name)
            if not isinstance(value, bytes) or len(value) != expected_lengths[field_name]:
                raise CaptureAuthorityError(f"invalid capture measurement {field_name}")

    @classmethod
    def from_bytes(cls, data: bytes, *, inline_bytes: int) -> CaptureMeasurement:
        if not isinstance(data, bytes):
            raise CaptureAuthorityError("capture measurement source must be bytes")
        if not _is_plain_int(inline_bytes, minimum=1):
            raise CaptureAuthorityError("inline byte bound must be positive")
        head_limit = (2 * inline_bytes) // 3
        tail_limit = inline_bytes - head_limit
        return cls(
            total_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            inline_bytes=inline_bytes,
            inline=data[: inline_bytes + 1],
            head=data[:head_limit],
            tail=data[-tail_limit:] if tail_limit else b"",
        )


@dataclass(frozen=True, slots=True)
class CaptureManifestWire:
    schema_version: int
    producer: str
    capture_id: str
    incarnation: str
    finalized_at_revision: int
    project_identity: tuple[int, int]
    root_identity: tuple[int, int]
    carrier_name: str
    carrier_identity: tuple[int, int]
    stream_domain: str
    total_bytes: int
    sha256: str
    inline_length: int
    head_length: int
    tail_length: int
    capture_status: _lifecycle_policy.CaptureStatus
    command_outcome_kind: CommandOutcomeKind
    command_outcome_value: int
    finalized_at: float
    reference_hash: str | None
    reference_expiry: float | None
    retention_deadline: float

    def __post_init__(self) -> None:
        _validate_manifest(self)

    @property
    def command_outcome(self) -> CommandOutcome:
        return CommandOutcome(self.command_outcome_kind, self.command_outcome_value)


@dataclass(frozen=True, slots=True)
class CaptureFinalManifest(_NoAuthorityCopy):
    schema_version: int
    producer: str
    capture_id: str
    incarnation: str
    finalized_at_revision: int
    project_identity: tuple[int, int]
    root_identity: tuple[int, int]
    carrier_name: str
    carrier_identity: tuple[int, int]
    stream_domain: str
    total_bytes: int
    sha256: str
    inline_length: int
    head_length: int
    tail_length: int
    capture_status: _lifecycle_policy.CaptureStatus
    command_outcome: CommandOutcome
    finalized_at: float
    reference_hash: str | None
    reference_expiry: float | None
    retention_deadline: float
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        _validate_manifest(self)


@dataclass(frozen=True, slots=True)
class CaptureWriteAuthority(_NoAuthorityCopy):
    capture_id: str
    incarnation: str
    expected_revision: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        if (
            not _syntax.CAPTURE_ID_RE.fullmatch(self.capture_id)
            or not _syntax.INCARNATION_RE.fullmatch(self.incarnation)
            or not _is_plain_int(self.expected_revision, minimum=1)
        ):
            raise CaptureAuthorityError("invalid capture write authority")


@dataclass(frozen=True, slots=True)
class VerifiedCaptureSnapshot(_NoAuthorityCopy):
    manifest: CaptureFinalManifest
    measurement: CaptureMeasurement
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        if (
            type(self.manifest) is not CaptureFinalManifest
            or type(self.measurement) is not CaptureMeasurement
            or self.manifest.total_bytes != self.measurement.total_bytes
            or self.manifest.sha256 != self.measurement.sha256
            or self.manifest.inline_length != len(self.measurement.inline)
            or self.manifest.head_length != len(self.measurement.head)
            or self.manifest.tail_length != len(self.measurement.tail)
        ):
            raise CaptureAuthorityError("verified snapshot does not match its measurement")


@dataclass(frozen=True, slots=True)
class CaptureReferenceHint:
    capture_id: str
    incarnation: str
    token: str


@dataclass(frozen=True, slots=True)
class IssuedCaptureReference(_NoAuthorityCopy):
    snapshot: VerifiedCaptureSnapshot
    token: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        hint = parse_capture_reference(self.token)
        if (
            type(self.snapshot) is not VerifiedCaptureSnapshot
            or hint.capture_id != self.snapshot.manifest.capture_id
            or hint.incarnation != self.snapshot.manifest.incarnation
        ):
            raise CaptureAuthorityError("issued reference does not match snapshot")


@dataclass(frozen=True, slots=True)
class PublishedCaptureReference(_NoAuthorityCopy, CaptureV2Renderable):
    snapshot: VerifiedCaptureSnapshot
    token: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        hint = parse_capture_reference(self.token)
        if (
            type(self.snapshot) is not VerifiedCaptureSnapshot
            or hint.capture_id != self.snapshot.manifest.capture_id
            or hint.incarnation != self.snapshot.manifest.incarnation
        ):
            raise CaptureAuthorityError("published reference does not match snapshot")

    def capture_v2_fields(self) -> CaptureV2Fields:
        return capture_v2_fields(
            self.snapshot,
            reference_status="published",
            reference=self.token,
            unavailable_reason=None,
        )


@dataclass(frozen=True, slots=True)
class UnavailableCaptureReference(_NoAuthorityCopy, CaptureV2Renderable):
    snapshot: VerifiedCaptureSnapshot
    reason_code: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        if (
            type(self.snapshot) is not VerifiedCaptureSnapshot
            or not isinstance(self.reason_code, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.reason_code)
        ):
            raise CaptureAuthorityError("invalid unavailable capture reference")

    def capture_v2_fields(self) -> CaptureV2Fields:
        return capture_v2_fields(
            self.snapshot,
            reference_status="unavailable",
            reference=None,
            unavailable_reason=self.reason_code,
        )


@dataclass(frozen=True, slots=True)
class FinalizedCapture(_NoAuthorityCopy):
    snapshot: VerifiedCaptureSnapshot
    finalized_at_revision: int
    issuance: IssuedCaptureReference | None
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        _require_factory(_factory_token, type(self).__name__)
        if (
            type(self.snapshot) is not VerifiedCaptureSnapshot
            or self.finalized_at_revision != self.snapshot.manifest.finalized_at_revision
            or (
                self.issuance is not None
                and (
                    type(self.issuance) is not IssuedCaptureReference
                    or self.issuance.snapshot is not self.snapshot
                )
            )
        ):
            raise CaptureAuthorityError("invalid finalized capture")


def _validate_manifest(value: CaptureFinalManifest | CaptureManifestWire) -> None:
    if (
        value.schema_version != SNAPSHOT_SCHEMA_VERSION
        or value.producer != SNAPSHOT_PRODUCER
        or not _syntax.CAPTURE_ID_RE.fullmatch(value.capture_id)
        or not _syntax.INCARNATION_RE.fullmatch(value.incarnation)
        or not _is_plain_int(value.finalized_at_revision, minimum=1)
        or not _CARRIER_NAME_RE.fullmatch(value.carrier_name)
        or value.carrier_name != f"shell_{value.capture_id}.log"
        or value.stream_domain != MANAGED_STREAM_DOMAIN
        or not _is_plain_int(value.total_bytes)
        or not isinstance(value.sha256, str)
        or not _syntax.SHA256_RE.fullmatch(value.sha256)
        or not _is_plain_int(value.inline_length)
        or not _is_plain_int(value.head_length)
        or not _is_plain_int(value.tail_length)
        or value.inline_length > value.total_bytes
        or value.head_length > value.total_bytes
        or value.tail_length > value.total_bytes
        or value.capture_status is not _lifecycle_policy.CaptureStatus.COMPLETE
    ):
        raise CaptureAuthorityError("invalid capture manifest fields")
    _identity(value.project_identity, "project identity")
    _identity(value.root_identity, "root identity")
    _identity(value.carrier_identity, "carrier identity")
    outcome = (
        value.command_outcome if isinstance(value, CaptureManifestWire) else value.command_outcome
    )
    if type(outcome) is not CommandOutcome:
        raise CaptureAuthorityError("invalid manifest command outcome")
    finalized_at = _finite(value.finalized_at, "finalization timestamp")
    retention_deadline = _finite(value.retention_deadline, "retention deadline")
    if retention_deadline < finalized_at:
        raise CaptureAuthorityError("retention deadline precedes finalization")
    if (value.reference_hash is None) != (value.reference_expiry is None):
        raise CaptureAuthorityError("incomplete capture reference binding")
    if value.reference_hash is not None:
        if not _syntax.SHA256_RE.fullmatch(value.reference_hash):
            raise CaptureAuthorityError("invalid capture reference hash")
        expiry = _finite(value.reference_expiry, "reference expiry")
        if expiry < finalized_at or expiry > retention_deadline:
            raise CaptureAuthorityError("invalid capture reference expiry")


def _make_manifest(
    *,
    capture_id: str,
    incarnation: str,
    finalized_at_revision: int,
    project_identity: tuple[int, int],
    root_identity: tuple[int, int],
    carrier_name: str,
    carrier_identity: tuple[int, int],
    measurement: CaptureMeasurement,
    command_outcome: CommandOutcome,
    finalized_at: float,
    reference_hash: str | None,
    reference_expiry: float | None,
    retention_deadline: float,
) -> CaptureFinalManifest:
    return CaptureFinalManifest(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        producer=SNAPSHOT_PRODUCER,
        capture_id=capture_id,
        incarnation=incarnation,
        finalized_at_revision=finalized_at_revision,
        project_identity=project_identity,
        root_identity=root_identity,
        carrier_name=carrier_name,
        carrier_identity=carrier_identity,
        stream_domain=MANAGED_STREAM_DOMAIN,
        total_bytes=measurement.total_bytes,
        sha256=measurement.sha256,
        inline_length=len(measurement.inline),
        head_length=len(measurement.head),
        tail_length=len(measurement.tail),
        capture_status=_lifecycle_policy.CaptureStatus.COMPLETE,
        command_outcome=command_outcome,
        finalized_at=finalized_at,
        reference_hash=reference_hash,
        reference_expiry=reference_expiry,
        retention_deadline=retention_deadline,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def _make_write_authority(
    capture_id: str,
    incarnation: str,
    expected_revision: int,
) -> CaptureWriteAuthority:
    return CaptureWriteAuthority(
        capture_id=capture_id,
        incarnation=incarnation,
        expected_revision=expected_revision,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def _make_snapshot(
    manifest: CaptureFinalManifest,
    measurement: CaptureMeasurement,
) -> VerifiedCaptureSnapshot:
    return VerifiedCaptureSnapshot(
        manifest=manifest,
        measurement=measurement,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def verify_capture_descriptor(fd: int, manifest: CaptureFinalManifest) -> None:
    if not _is_plain_int(fd) or type(manifest) is not CaptureFinalManifest:
        raise CaptureAuthorityError("invalid capture descriptor authority")
    _descriptor.verify_capture_descriptor(
        fd,
        manifest,
        error_type=CaptureAuthorityError,
    )


def verify_capture_snapshot(
    *,
    fd: int,
    capture_id: str,
    incarnation: str,
    project_identity: tuple[int, int],
    root_identity: tuple[int, int],
    carrier_name: str,
    carrier_identity: tuple[int, int],
    measurement: CaptureMeasurement,
    command_outcome: CommandOutcome,
    expected_revision: int,
    finalized_at: float,
    retention_deadline: float,
) -> VerifiedCaptureSnapshot:
    """Verify one immutable carrier view and sync it before FINAL is possible."""

    if not _is_plain_int(fd):
        raise CaptureAuthorityError("invalid capture descriptor")
    if type(measurement) is not CaptureMeasurement:
        raise CaptureAuthorityError("measurement must be an exact CaptureMeasurement")
    if type(command_outcome) is not CommandOutcome:
        raise CaptureAuthorityError("outcome must be an exact CommandOutcome")
    expected_identity = _identity(carrier_identity, "carrier identity")
    project = _identity(project_identity, "project identity")
    root = _identity(root_identity, "root identity")
    try:
        before = os.fstat(fd)
    except OSError as exc:
        raise CaptureAuthorityError("cannot inspect capture descriptor") from exc
    actual_identity = (before.st_dev, before.st_ino)
    if actual_identity != expected_identity:
        raise CaptureAuthorityError("capture artifact identity changed")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_mode & _UNTRUSTED_MODE_BITS
        or before.st_size != measurement.total_bytes
    ):
        raise CaptureAuthorityError("capture artifact metadata changed")
    digest = hashlib.sha256()
    head = bytearray()
    inline = bytearray()
    tail = bytearray()
    offset = 0
    while offset < measurement.total_bytes:
        try:
            chunk = os.pread(
                fd,
                min(_READ_CHUNK_BYTES, measurement.total_bytes - offset),
                offset,
            )
        except OSError as exc:
            raise CaptureAuthorityError("capture artifact readback failed") from exc
        if not chunk:
            raise CaptureAuthorityError("capture artifact readback ended early")
        digest.update(chunk)
        if len(head) < len(measurement.head):
            head.extend(chunk[: len(measurement.head) - len(head)])
        if len(inline) < len(measurement.inline):
            inline.extend(chunk[: len(measurement.inline) - len(inline)])
        if measurement.tail:
            tail.extend(chunk)
            if len(tail) > len(measurement.tail):
                del tail[: -len(measurement.tail)]
        offset += len(chunk)

    if not hmac.compare_digest(digest.hexdigest(), measurement.sha256):
        raise CaptureAuthorityError("capture artifact content changed")
    if (
        bytes(head) != measurement.head
        or bytes(inline) != measurement.inline
        or bytes(tail) != measurement.tail
    ):
        raise CaptureAuthorityError("capture artifact preview changed")
    try:
        after = os.fstat(fd)
    except OSError as exc:
        raise CaptureAuthorityError("cannot re-inspect capture descriptor") from exc
    if (
        (after.st_dev, after.st_ino) != expected_identity
        or after.st_size != measurement.total_bytes
        or after.st_nlink != 1
    ):
        raise CaptureAuthorityError("capture artifact metadata changed")
    try:
        os.fsync(fd)
    except OSError as exc:
        raise CaptureAuthorityError("cannot sync completed capture artifact") from exc
    manifest = _make_manifest(
        capture_id=capture_id,
        incarnation=incarnation,
        finalized_at_revision=expected_revision + 1,
        project_identity=project,
        root_identity=root,
        carrier_name=carrier_name,
        carrier_identity=expected_identity,
        measurement=measurement,
        command_outcome=command_outcome,
        finalized_at=_finite(finalized_at, "finalization timestamp"),
        reference_hash=None,
        reference_expiry=None,
        retention_deadline=_finite(retention_deadline, "retention deadline"),
    )
    return _make_snapshot(manifest, measurement)


def _manifest_primitive(manifest: CaptureFinalManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "producer": manifest.producer,
        "capture_id": manifest.capture_id,
        "incarnation": manifest.incarnation,
        "finalized_at_revision": manifest.finalized_at_revision,
        "project_identity": list(manifest.project_identity),
        "root_identity": list(manifest.root_identity),
        "carrier_name": manifest.carrier_name,
        "carrier_identity": list(manifest.carrier_identity),
        "stream_domain": manifest.stream_domain,
        "total_bytes": manifest.total_bytes,
        "sha256": manifest.sha256,
        "inline_length": manifest.inline_length,
        "head_length": manifest.head_length,
        "tail_length": manifest.tail_length,
        "capture_status": manifest.capture_status.value,
        "command_outcome_kind": manifest.command_outcome.kind.value,
        "command_outcome_value": manifest.command_outcome.value,
        "finalized_at": manifest.finalized_at,
        "reference_hash": manifest.reference_hash,
        "reference_expiry": manifest.reference_expiry,
        "retention_deadline": manifest.retention_deadline,
    }


def encode_capture_final_manifest(manifest: CaptureFinalManifest) -> bytes:
    if type(manifest) is not CaptureFinalManifest:
        raise CaptureAuthorityError("manifest must be an exact CaptureFinalManifest")
    encoded = _canonical_json(_manifest_primitive(manifest))
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise CaptureAuthorityError("capture manifest exceeds bound")
    return encoded


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


def decode_capture_manifest_wire(data: bytes) -> CaptureManifestWire:
    """Strictly decode primitive data without granting capture authority."""

    if not isinstance(data, bytes) or not data or len(data) > MAX_MANIFEST_BYTES:
        raise CaptureAuthorityError("capture manifest wire size is invalid")
    try:
        text = data.decode("utf-8", errors="strict")
        primitive = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateField as exc:
        raise CaptureAuthorityError("duplicate capture manifest field") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise CaptureAuthorityError("invalid capture manifest encoding") from exc
    if _canonical_json(primitive) != data:
        raise CaptureAuthorityError("capture manifest is not canonical")
    if not isinstance(primitive, dict) or set(primitive) != _MANIFEST_FIELDS:
        raise CaptureAuthorityError("capture manifest fields do not match schema")
    try:
        wire = CaptureManifestWire(
            schema_version=primitive["schema_version"],
            producer=primitive["producer"],
            capture_id=primitive["capture_id"],
            incarnation=primitive["incarnation"],
            finalized_at_revision=primitive["finalized_at_revision"],
            project_identity=_identity(
                primitive["project_identity"], "project identity", wire=True
            ),
            root_identity=_identity(primitive["root_identity"], "root identity", wire=True),
            carrier_name=primitive["carrier_name"],
            carrier_identity=_identity(
                primitive["carrier_identity"], "carrier identity", wire=True
            ),
            stream_domain=primitive["stream_domain"],
            total_bytes=primitive["total_bytes"],
            sha256=primitive["sha256"],
            inline_length=primitive["inline_length"],
            head_length=primitive["head_length"],
            tail_length=primitive["tail_length"],
            capture_status=_lifecycle_policy.CaptureStatus(primitive["capture_status"]),
            command_outcome_kind=CommandOutcomeKind(primitive["command_outcome_kind"]),
            command_outcome_value=primitive["command_outcome_value"],
            finalized_at=primitive["finalized_at"],
            reference_hash=primitive["reference_hash"],
            reference_expiry=primitive["reference_expiry"],
            retention_deadline=primitive["retention_deadline"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureAuthorityError("invalid capture manifest fields") from exc
    _validate_manifest(wire)
    return wire


def _restore_capture_final_manifest(wire: CaptureManifestWire) -> CaptureFinalManifest:
    if type(wire) is not CaptureManifestWire:
        raise CaptureAuthorityError("manifest restoration requires strict wire data")
    _validate_manifest(wire)
    return CaptureFinalManifest(
        schema_version=wire.schema_version,
        producer=wire.producer,
        capture_id=wire.capture_id,
        incarnation=wire.incarnation,
        finalized_at_revision=wire.finalized_at_revision,
        project_identity=wire.project_identity,
        root_identity=wire.root_identity,
        carrier_name=wire.carrier_name,
        carrier_identity=wire.carrier_identity,
        stream_domain=wire.stream_domain,
        total_bytes=wire.total_bytes,
        sha256=wire.sha256,
        inline_length=wire.inline_length,
        head_length=wire.head_length,
        tail_length=wire.tail_length,
        capture_status=wire.capture_status,
        command_outcome=wire.command_outcome,
        finalized_at=wire.finalized_at,
        reference_hash=wire.reference_hash,
        reference_expiry=wire.reference_expiry,
        retention_deadline=wire.retention_deadline,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def parse_capture_reference(token: str) -> CaptureReferenceHint:
    if not isinstance(token, str):
        raise CaptureAuthorityError("invalid capture reference")
    try:
        encoded = token.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise CaptureAuthorityError("invalid capture reference") from exc
    if len(encoded) > MAX_REFERENCE_TOKEN_BYTES:
        raise CaptureAuthorityError("invalid capture reference")
    matched = _syntax.REFERENCE_RE.fullmatch(token)
    if matched is None:
        raise CaptureAuthorityError("invalid capture reference")
    return CaptureReferenceHint(
        capture_id=matched.group(1),
        incarnation=matched.group(2),
        token=token,
    )


def _reference_context(
    manifest: CaptureFinalManifest,
    *,
    reference_expiry: float | None = None,
) -> bytes:
    primitive = _manifest_primitive(manifest)
    primitive["reference_hash"] = None
    if reference_expiry is not None:
        primitive["reference_expiry"] = reference_expiry
    return b"autoskillit:capture-reference:v2\0" + _canonical_json(primitive)


def _reference_hash(
    token: str,
    manifest: CaptureFinalManifest,
    *,
    reference_expiry: float | None = None,
) -> str:
    parse_capture_reference(token)
    digest = hashlib.sha256()
    digest.update(_reference_context(manifest, reference_expiry=reference_expiry))
    digest.update(b"\0")
    digest.update(token.encode("ascii"))
    return digest.hexdigest()


def _issue_capture_reference(
    snapshot: VerifiedCaptureSnapshot,
    *,
    expiry: float,
) -> tuple[str, str]:
    manifest = snapshot.manifest
    token = f"ascr2:{manifest.capture_id}:{manifest.incarnation}:{secrets.token_hex(32)}"
    return token, _reference_hash(
        token,
        manifest,
        reference_expiry=expiry,
    )


def _bind_finalized_snapshot(
    verified: VerifiedCaptureSnapshot,
    *,
    reference_token: str | None,
    reference_hash: str | None,
    reference_expiry: float | None,
) -> FinalizedCapture:
    if type(verified) is not VerifiedCaptureSnapshot:
        raise CaptureAuthorityError("finalization requires a verified snapshot")
    if (reference_token is None) != (reference_hash is None):
        raise CaptureAuthorityError("incomplete issued reference")
    base = verified.manifest
    if reference_token is not None:
        if reference_hash is None:
            raise CaptureAuthorityError("incomplete issued reference")
        if not hmac.compare_digest(
            _reference_hash(
                reference_token,
                base,
                reference_expiry=reference_expiry,
            ),
            reference_hash,
        ):
            raise CaptureAuthorityError("issued reference hash does not match")
    manifest = _make_manifest(
        capture_id=base.capture_id,
        incarnation=base.incarnation,
        finalized_at_revision=base.finalized_at_revision,
        project_identity=base.project_identity,
        root_identity=base.root_identity,
        carrier_name=base.carrier_name,
        carrier_identity=base.carrier_identity,
        measurement=verified.measurement,
        command_outcome=base.command_outcome,
        finalized_at=base.finalized_at,
        reference_hash=reference_hash,
        reference_expiry=reference_expiry,
        retention_deadline=base.retention_deadline,
    )
    snapshot = _make_snapshot(manifest, verified.measurement)
    issuance = (
        None
        if reference_token is None
        else IssuedCaptureReference(
            snapshot=snapshot,
            token=reference_token,
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    )
    return FinalizedCapture(
        snapshot=snapshot,
        finalized_at_revision=manifest.finalized_at_revision,
        issuance=issuance,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def _make_published_reference(
    issuance: IssuedCaptureReference,
) -> PublishedCaptureReference:
    if type(issuance) is not IssuedCaptureReference:
        raise CaptureAuthorityError("publication requires an issued reference")
    return PublishedCaptureReference(
        snapshot=issuance.snapshot,
        token=issuance.token,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def _make_unavailable_reference(
    snapshot: VerifiedCaptureSnapshot,
    reason_code: str,
) -> UnavailableCaptureReference:
    return UnavailableCaptureReference(
        snapshot=snapshot,
        reason_code=reason_code,
        _factory_token=_AUTHORITY_FACTORY_TOKEN,
    )


def _reference_matches(token: str, manifest: CaptureFinalManifest) -> bool:
    if manifest.reference_hash is None:
        return False
    try:
        actual = _reference_hash(token, manifest)
    except CaptureAuthorityError:
        return False
    return hmac.compare_digest(actual, manifest.reference_hash)

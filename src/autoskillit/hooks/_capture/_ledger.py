"""Framed-binary-codec facade for shell-capture lifecycle state.

Lifecycle-record types, the canonical-JSON encoder, and the
``LedgerCodecError`` / ``CaptureTransitionCommittedError`` exception classes
live in the sibling module ``_lifecycle_record.py``. This facade owns the
framed-binary codec (``encode_frame``, ``decode_ledger``, ``write_all``),
its data classes (``LedgerFrame``, ``DecodedLedger``), the frame-format
constants, and the ``UnsupportedLedgerVersionError`` exception class
(which subclasses ``LedgerCodecError`` and surfaces from ``decode_ledger``
when an intact frame requires a newer reader).

The dependency direction is strictly ``_ledger -> _lifecycle_record``
(one-way): every record-codec symbol this facade exposes is imported from
the sibling. ``UnsupportedLedgerVersionError`` lives here because it is a
framing concern, but its base class ``LedgerCodecError`` is imported from
the sibling.

Stdlib-only at runtime -- sibling imports from ``_lifecycle_record`` are
intra-``_capture/`` (no cross-``hooks/`` boundary), so the simple
sibling-import pattern is sufficient (no three-way discriminator needed).
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from typing import cast

from ._lifecycle_record import (
    CaptureDeliveryStatus as _CaptureDeliveryStatus,
)

# Lifecycle-record types, canonical-JSON encoder, and exception class
# live in the sibling module. The facade imports them here so the
# framed-binary codec can call canonical_json and raise LedgerCodecError
# without circular import risk (one-way: _ledger -> _lifecycle_record).
from ._lifecycle_record import (
    CaptureLifecycleRecord,
    CaptureTransitionCommittedError,
    LedgerCodecError,
    _decode_json,
    adopted_orphan_record,
    canonical_json,
    is_delivery_successor,
    is_reference_successor,
    is_retention_successor,
    is_state_successor,
    legacy_record_from_dict,
    record_from_dict,
    record_to_dict,
    same_record,
    validate_record,
    validate_successor,
)
from ._lifecycle_record import (
    CaptureReferenceStatus as _CaptureReferenceStatus,
)
from ._lifecycle_record import (
    CaptureRetentionPhase as _CaptureRetentionPhase,
)
from ._lifecycle_record import (
    CaptureSnapshotStatus as _CaptureSnapshotStatus,
)
from ._lifecycle_record import (
    CaptureState as _CaptureState,
)
from ._lifecycle_record import (
    CaptureStatus as _CaptureStatus,
)
from ._module_identity import register_module_aliases

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
    "CaptureState",
    "CaptureStatus",
    "CaptureTransitionCommittedError",
    "DecodedLedger",
    "LedgerCodecError",
    "LedgerFrame",
    "UnsupportedLedgerVersionError",
    "adopted_orphan_record",
    "canonical_json",
    "decode_ledger",
    "encode_frame",
    "is_delivery_successor",
    "is_reference_successor",
    "is_retention_successor",
    "is_state_successor",
    "legacy_record_from_dict",
    "record_from_dict",
    "record_to_dict",
    "same_record",
    "validate_record",
    "validate_successor",
    "write_all",
]


CaptureState = _CaptureState
CaptureReferenceStatus = _CaptureReferenceStatus
CaptureDeliveryStatus = _CaptureDeliveryStatus
CaptureRetentionPhase = _CaptureRetentionPhase
CaptureSnapshotStatus = _CaptureSnapshotStatus
CaptureStatus = _CaptureStatus


FRAME_MAGIC = b"ASCL"
CURRENT_FORMAT_VERSION = 2
MAX_FRAME_BYTES = 64 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
_FRAME_HEADER_FORMAT = ">4sI"
_FRAME_HEADER_BYTES = 8
_CHECKSUM_BYTES = hashlib.sha256().digest_size


class UnsupportedLedgerVersionError(LedgerCodecError):
    """Raised when an intact frame requires a newer ledger reader."""

    reason = "unsupported_future"

    def __init__(self, observed_version: int) -> None:
        self.observed_version = observed_version
        self.current_version = CURRENT_FORMAT_VERSION
        super().__init__(
            "unsupported future lifecycle frame format version "
            f"{observed_version} (current {CURRENT_FORMAT_VERSION})"
        )


@dataclass(frozen=True, slots=True)
class LedgerFrame:
    format_version: int
    compaction_epoch: int
    record: dict[str, object]
    canonical_payload: bytes
    exact_bytes: bytes


@dataclass(frozen=True, slots=True)
class DecodedLedger:
    frames: tuple[LedgerFrame, ...]
    truncate_at: int | None


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
            isinstance(version, int)
            and not isinstance(version, bool)
            and version > CURRENT_FORMAT_VERSION
        ):
            raise UnsupportedLedgerVersionError(version)
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
                exact_bytes=data[cursor:frame_end],
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

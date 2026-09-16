"""Stdlib-only verification for one retained shell-capture descriptor."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ._failure_policy import CaptureFailureReason
    from ._snapshot import CaptureMeasurement

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_READ_CHUNK_BYTES = 64 * 1024
_UNTRUSTED_MODE_BITS = stat.S_IRWXG | stat.S_IRWXO


class CaptureAuthorityError(RuntimeError):
    """Raised when shell-capture authority cannot be proven."""

    failure_reason: CaptureFailureReason | None = None


def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CaptureAuthorityError("value is not canonically encodable") from exc


class CaptureManifest(Protocol):
    @property
    def carrier_identity(self) -> tuple[int, int]: ...

    @property
    def total_bytes(self) -> int: ...

    @property
    def sha256(self) -> str: ...


def inspect_capture_descriptor(
    fd: int,
    manifest: CaptureManifest,
    *,
    error_type: type[RuntimeError],
) -> os.stat_result:
    try:
        value = os.fstat(fd)
    except OSError as exc:
        raise error_type("cannot inspect capture carrier") from exc
    if (
        (value.st_dev, value.st_ino) != manifest.carrier_identity
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_uid != os.geteuid()
        or value.st_mode & _UNTRUSTED_MODE_BITS
        or value.st_size != manifest.total_bytes
    ):
        raise error_type("capture carrier metadata changed")
    return value


def verify_capture_descriptor(
    fd: int,
    manifest: CaptureManifest,
    *,
    error_type: type[RuntimeError],
) -> None:
    inspect_capture_descriptor(fd, manifest, error_type=error_type)
    digest = hashlib.sha256()
    offset = 0
    while offset < manifest.total_bytes:
        try:
            chunk = os.pread(
                fd,
                min(_READ_CHUNK_BYTES, manifest.total_bytes - offset),
                offset,
            )
        except OSError as exc:
            raise error_type("capture carrier readback failed") from exc
        if not chunk:
            raise error_type("capture carrier readback ended early")
        digest.update(chunk)
        offset += len(chunk)
    if not hmac.compare_digest(digest.hexdigest(), manifest.sha256):
        raise error_type("capture carrier content changed")
    inspect_capture_descriptor(fd, manifest, error_type=error_type)


def _read_capture_preview(
    fd: int,
    measurement: CaptureMeasurement,
    *,
    error_factory: Callable[[str], CaptureAuthorityError],
) -> tuple[str, bytes, bytes, bytes]:
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
            raise error_factory("capture artifact readback failed") from exc
        if not chunk:
            raise error_factory("capture artifact readback ended early")
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
    return digest.hexdigest(), bytes(head), bytes(inline), bytes(tail)


def verify_capture_measurement(
    fd: int,
    measurement: CaptureMeasurement,
    carrier_identity: tuple[int, int],
    *,
    error_factory: Callable[[str], CaptureAuthorityError],
) -> None:
    try:
        before = os.fstat(fd)
    except OSError as exc:
        raise error_factory("cannot inspect capture descriptor") from exc
    actual_identity = (before.st_dev, before.st_ino)
    if actual_identity != carrier_identity:
        raise error_factory("capture artifact identity changed")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_mode & _UNTRUSTED_MODE_BITS
        or before.st_size != measurement.total_bytes
    ):
        raise error_factory("capture artifact metadata changed")
    digest, head, inline, tail = _read_capture_preview(
        fd,
        measurement,
        error_factory=error_factory,
    )
    if not hmac.compare_digest(digest, measurement.sha256):
        raise error_factory("capture artifact content changed")
    if head != measurement.head or inline != measurement.inline or tail != measurement.tail:
        raise error_factory("capture artifact preview changed")
    try:
        after = os.fstat(fd)
    except OSError as exc:
        raise error_factory("cannot re-inspect capture descriptor") from exc
    if (
        (after.st_dev, after.st_ino) != carrier_identity
        or after.st_size != measurement.total_bytes
        or after.st_nlink != 1
    ):
        raise error_factory("capture artifact metadata changed")
    try:
        os.fsync(fd)
    except OSError as exc:
        raise error_factory("cannot sync completed capture artifact") from exc

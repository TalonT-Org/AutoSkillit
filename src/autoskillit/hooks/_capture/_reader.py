"""Self-contained verified shell-capture reader primitives."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import sys
from dataclasses import InitVar, dataclass, field
from typing import NoReturn, Protocol, SupportsIndex

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._reader", "autoskillit.hooks._capture._reader"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture reader module identity")

__all__ = [
    "MAX_VERIFIED_READ_BYTES",
    "CaptureAuthorityError",
    "VerifiedCaptureReader",
]

MAX_VERIFIED_READ_BYTES = 64 * 1024
_MAX_OFFSET = (1 << 63) - 1
_READ_CHUNK_BYTES = 64 * 1024
_UNTRUSTED_MODE_BITS = stat.S_IRWXG | stat.S_IRWXO
_READER_FACTORY_TOKEN = object()


class CaptureAuthorityError(RuntimeError):
    """Raised when shell-capture authority cannot be proven."""


class CaptureManifest(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def incarnation(self) -> str: ...

    @property
    def carrier_identity(self) -> tuple[int, int]: ...

    @property
    def total_bytes(self) -> int: ...

    @property
    def sha256(self) -> str: ...


@dataclass(frozen=True, slots=True)
class VerifiedCaptureReader:
    """Read-only bounded API over one retained, verified open file description."""

    manifest: CaptureManifest
    revision: int
    _descriptor: int = field(repr=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _READER_FACTORY_TOKEN:
            raise CaptureAuthorityError("VerifiedCaptureReader must be factory-created")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
            or not isinstance(self._descriptor, int)
            or isinstance(self._descriptor, bool)
            or self._descriptor < 0
        ):
            raise CaptureAuthorityError("invalid verified capture reader")

    def __enter__(self) -> VerifiedCaptureReader:
        if self._descriptor < 0:
            raise CaptureAuthorityError("verified capture reader is closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __copy__(self) -> NoReturn:
        raise CaptureAuthorityError("verified capture reader cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise CaptureAuthorityError("verified capture reader cannot be deep-copied")

    def __reduce__(self) -> NoReturn:
        raise CaptureAuthorityError("verified capture reader cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise CaptureAuthorityError("verified capture reader cannot be pickled")

    def read(self, offset: int, length: int) -> bytes:
        if self._descriptor < 0:
            raise CaptureAuthorityError("verified capture reader is closed")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset > _MAX_OFFSET
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 0
            or length > MAX_VERIFIED_READ_BYTES
            or offset + length > _MAX_OFFSET
        ):
            raise CaptureAuthorityError("invalid verified capture read bound")
        if offset >= self.manifest.total_bytes:
            return b""
        length = min(length, self.manifest.total_bytes - offset)
        chunks: list[bytes] = []
        cursor = offset
        remaining = length
        while remaining:
            try:
                chunk = os.pread(
                    self._descriptor,
                    min(remaining, _READ_CHUNK_BYTES),
                    cursor,
                )
            except OSError as exc:
                raise CaptureAuthorityError("verified capture read failed") from exc
            if not chunk:
                break
            chunks.append(chunk)
            cursor += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        if self._descriptor >= 0:
            descriptor = self._descriptor
            object.__setattr__(self, "_descriptor", -1)
            os.close(descriptor)


def _inspect_manifest_descriptor(
    fd: int,
    manifest: CaptureManifest,
) -> os.stat_result:
    try:
        value = os.fstat(fd)
    except OSError as exc:
        raise CaptureAuthorityError("cannot inspect capture carrier") from exc
    if (
        (value.st_dev, value.st_ino) != manifest.carrier_identity
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_uid != os.geteuid()
        or value.st_mode & _UNTRUSTED_MODE_BITS
        or value.st_size != manifest.total_bytes
    ):
        raise CaptureAuthorityError("capture carrier metadata changed")
    return value


def _verify_manifest_descriptor(fd: int, manifest: CaptureManifest) -> None:
    _inspect_manifest_descriptor(fd, manifest)
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
            raise CaptureAuthorityError("capture carrier readback failed") from exc
        if not chunk:
            raise CaptureAuthorityError("capture carrier readback ended early")
        digest.update(chunk)
        offset += len(chunk)
    if not hmac.compare_digest(digest.hexdigest(), manifest.sha256):
        raise CaptureAuthorityError("capture carrier content changed")
    _inspect_manifest_descriptor(fd, manifest)


def _make_verified_reader(
    fd: int,
    manifest: CaptureManifest,
    revision: int,
) -> VerifiedCaptureReader:
    return VerifiedCaptureReader(
        manifest=manifest,
        revision=revision,
        _descriptor=fd,
        _factory_token=_READER_FACTORY_TOKEN,
    )

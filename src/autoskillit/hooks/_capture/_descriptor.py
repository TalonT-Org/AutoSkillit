"""Stdlib-only verification for one retained shell-capture descriptor."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
from typing import Protocol

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._descriptor", "autoskillit.hooks._capture._descriptor"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture descriptor module identity")

_READ_CHUNK_BYTES = 64 * 1024
_UNTRUSTED_MODE_BITS = stat.S_IRWXG | stat.S_IRWXO


class CaptureAuthorityError(RuntimeError):
    """Raised when shell-capture authority cannot be proven."""


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

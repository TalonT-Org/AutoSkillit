"""Budget-bounded, stdlib-only directory-reconciliation scan for shell-capture orphans.

Every deletion path in this package (`_sweep.py`) only ever acts on records the
ledger already knows about — a `shell_[0-9a-f]{16}.log` file written before a
crash, a ledger reset, or a legacy pre-ledger run has no record and is
permanently invisible to cleanup. This module performs the read-only half of
reconciling that gap: it walks the capture directory, in budget-bounded
batches resumed via a persisted cursor, and returns the names of files that
look like abandoned capture artifacts. It never touches the ledger and never
deletes anything — adoption (turning a candidate name into a ledger record so
the existing quarantine-deletion path can retire it) is the sweep layer's job.
"""

from __future__ import annotations

import errno
import json
import math
import os
import stat
from collections.abc import Collection
from dataclasses import dataclass

from . import _control_file, _ledger, _syntax
from ._module_identity import register_module_aliases
from ._types import SweepBudgetSpec

register_module_aliases(__name__)

__all__ = [
    "ADOPTION_AGE_SECONDS",
    "CURSOR_NAME",
    "OrphanScanAuthorityError",
    "OrphanScanResult",
    "clear_cursor",
    "load_cursor",
    "scan_for_orphans",
    "write_cursor",
]

CURSOR_NAME = ".orphan-scan-cursor"

# Comfortably beyond the one-hour finalize/abandon eligibility grace
# (`_capture_lifecycle.py::_RETENTION_SECONDS`) so a file that is merely
# mid-write when the scan passes over it is never a candidate.
ADOPTION_AGE_SECONDS = 24 * 3600.0

_NAME_RE = _syntax.PUBLIC_NAME_RE
_MAX_CURSOR_BYTES = 512
_VERSION = 1
_NOFOLLOW = os.O_NOFOLLOW
_CLOEXEC = os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _CLOEXEC | _NOFOLLOW


class OrphanScanAuthorityError(OSError):
    pass


@dataclass(frozen=True, slots=True)
class OrphanScanResult:
    candidates: tuple[str, ...]
    examined: int
    directory_complete: bool


def _validate_file(value: os.stat_result) -> None:
    _control_file.validate_private_file(
        value,
        OrphanScanAuthorityError(errno.ELOOP, "unsafe orphan-scan cursor"),
    )


def _observe(root_fd: int) -> os.stat_result | None:
    try:
        value = os.stat(CURSOR_NAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OrphanScanAuthorityError(
            exc.errno,
            "cannot inspect orphan-scan cursor",
        ) from exc
    _validate_file(value)
    return value


def _read_all(fd: int) -> bytes:
    payload = bytearray()
    while len(payload) <= _MAX_CURSOR_BYTES:
        chunk = os.read(fd, _MAX_CURSOR_BYTES + 1 - len(payload))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
    return bytes(payload)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def load_cursor(root_fd: int) -> str | None:
    """Return the last-examined (sorted) directory-entry name, or None."""
    observed = _observe(root_fd)
    if observed is None:
        return None
    try:
        fd = os.open(CURSOR_NAME, _READ_FLAGS, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise OrphanScanAuthorityError(
                exc.errno,
                "unsafe orphan-scan cursor",
            ) from exc
        raise OrphanScanAuthorityError(
            exc.errno,
            "cannot open orphan-scan cursor",
        ) from exc
    try:
        current = os.fstat(fd)
        _validate_file(current)
        if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
            raise OrphanScanAuthorityError(
                errno.ELOOP,
                "orphan-scan cursor identity changed",
            )
        payload = _read_all(fd)
    finally:
        os.close(fd)
    if len(payload) > _MAX_CURSOR_BYTES:
        return None
    try:
        decoded = json.loads(payload)
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"last_name", "version"}
            or _canonical(decoded) != payload
            or decoded["version"] != _VERSION
            or not isinstance(decoded["last_name"], str)
            or not decoded["last_name"]
        ):
            return None
        last_name = decoded["last_name"]
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return last_name


def _write_payload(fd: int, payload: bytes) -> None:
    try:
        _ledger.write_all(fd, payload)
    except _ledger.LedgerCodecError as exc:
        raise OrphanScanAuthorityError(
            errno.EIO,
            "orphan-scan cursor write made no progress",
        ) from exc


def write_cursor(root_fd: int, *, last_name: str) -> None:
    _observe(root_fd)
    payload = _canonical({"last_name": last_name, "version": _VERSION})
    if len(payload) > _MAX_CURSOR_BYTES:
        raise OrphanScanAuthorityError("orphan-scan cursor exceeds bound")
    _control_file.publish_private_file(
        root_fd,
        target_name=CURSOR_NAME,
        temp_prefix=".orphan-scan-cursor-",
        payload=payload,
        validate_file=_validate_file,
        write_all=_write_payload,
    )


def clear_cursor(root_fd: int) -> bool:
    if _observe(root_fd) is None:
        return False
    try:
        os.unlink(CURSOR_NAME, dir_fd=root_fd)
    except OSError as exc:
        raise OrphanScanAuthorityError(
            exc.errno,
            "cannot remove orphan-scan cursor",
        ) from exc
    os.fsync(root_fd)
    return True


def _adoptable(root_fd: int, name: str, tracked: frozenset[str], now: float) -> bool:
    if _NAME_RE.fullmatch(name) is None or name in tracked:
        return False
    try:
        value = os.lstat(name, dir_fd=root_fd)
    except OSError:
        return False
    # `lstat` (never a following `stat`/`Path.is_file()`) is load-bearing: a
    # symlinked capture root let cleanup escape the project (#4319). Mtime age
    # alone is not a liveness signal either — it must combine with the
    # tracked-name exclusion above, since a quiet-but-live writer's file can
    # age past the threshold while still open (#4321).
    if not stat.S_ISREG(value.st_mode):
        return False
    return (now - value.st_mtime) >= ADOPTION_AGE_SECONDS


def scan_for_orphans(
    root_fd: int,
    tracked_artifact_names: Collection[str],
    budget: SweepBudgetSpec,
    *,
    now: float,
) -> OrphanScanResult:
    """Scan up to ``budget.max_directory_entries_scanned`` directory entries.

    Entries are visited in sorted-name order, resumed from a persisted cursor
    (the only stable, restartable position `os.scandir` supports) so repeated
    budget-bounded invocations cover the whole directory without rescanning
    from zero. Returns adoption candidates only — never deletes, never writes
    a ledger record.
    """
    if (
        type(budget) is not SweepBudgetSpec
        or budget.max_directory_entries_scanned <= 0
        or not isinstance(now, (int, float))
        or isinstance(now, bool)
        or not math.isfinite(now)
    ):
        return OrphanScanResult(candidates=(), examined=0, directory_complete=True)
    tracked = (
        tracked_artifact_names
        if isinstance(tracked_artifact_names, frozenset)
        else frozenset(tracked_artifact_names)
    )
    try:
        names = sorted(entry.name for entry in os.scandir(root_fd))
    except OSError as exc:
        raise OrphanScanAuthorityError(
            exc.errno,
            "cannot list capture directory",
        ) from exc
    cursor = load_cursor(root_fd)
    start = 0
    if cursor is not None:
        low, high = 0, len(names)
        while low < high:
            mid = (low + high) // 2
            if names[mid] <= cursor:
                low = mid + 1
            else:
                high = mid
        start = low
    limit = budget.max_directory_entries_scanned
    window = names[start : start + limit]
    directory_complete = start + len(window) >= len(names)
    candidates = tuple(name for name in window if _adoptable(root_fd, name, tracked, now))
    if window:
        if directory_complete:
            clear_cursor(root_fd)
        else:
            write_cursor(root_fd, last_name=window[-1])
    return OrphanScanResult(
        candidates=candidates,
        examined=len(window),
        directory_complete=directory_complete,
    )

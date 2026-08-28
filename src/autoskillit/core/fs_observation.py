"""Shared funnel for observing filesystem paths obtained by enumeration.

A path produced by ``os.walk``, ``glob``, ``rglob``, ``scandir``, or
``listdir`` is a claim about the past, not a fact about the present — the
entry may vanish between enumeration and the moment it is stat'd. Every
caller that walks a directory and then stats what it found must route the
observation through this module rather than calling ``.stat()``/``.lstat()``/
``os.path.getmtime()`` directly, so a concurrent deletion produces a defined
``None`` result instead of an uncaught ``OSError`` far up the call stack.

Stdlib-only: importable from hook subprocesses and every layer above IL-0.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = ["ObservedEntry", "VANISHED_ERRORS", "observe_path_mode", "safe_mtime", "scan_observed"]

# Both mean the path stopped resolving between enumeration and observation:
# FileNotFoundError when the leaf entry itself is gone, NotADirectoryError
# when an intermediate directory component was replaced by a regular file
# mid-walk (verified: lstat/stat/getmtime all raise NotADirectoryError, never
# FileNotFoundError, for that shape). Every other OSError — PermissionError
# in particular — propagates: a permissions or IO fault is a real failure and
# must not be laundered into "vanished".
VANISHED_ERRORS = (FileNotFoundError, NotADirectoryError)


@dataclass(frozen=True, slots=True)
class ObservedEntry:
    """A directory entry whose metadata was captured during enumeration.

    ``status`` is a single ``lstat`` result taken while the entry was being
    enumerated. It is a fact about that instant, not a promise about now --
    but it is already *observed*, so reading any field of it cannot raise.
    Callers needing a fresh reading after acquiring a lock must go back
    through :func:`safe_mtime` and handle ``None`` explicitly.

    The whole ``stat_result`` is carried rather than a chosen subset: the
    syscall already produced every field, and exposing only some of them
    would push the next caller who needs ``st_ctime`` or ``st_size`` back
    into re-stat'ing the path -- reintroducing exactly the hazard this type
    exists to remove.
    """

    path: Path
    status: os.stat_result

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def mode(self) -> int:
        return self.status.st_mode

    @property
    def mtime(self) -> float:
        return self.status.st_mtime

    @property
    def is_dir(self) -> bool:
        return stat.S_ISDIR(self.status.st_mode)

    @property
    def is_symlink(self) -> bool:
        return stat.S_ISLNK(self.status.st_mode)


def scan_observed(root: Path) -> Iterator[ObservedEntry]:
    """Enumerate *root*, yielding entries whose metadata is already observed.

    The fail-safe counterpart to :func:`autoskillit.core.io.strict_walk`:
    same "observe once during enumeration, never hand back a bare name"
    discipline, opposite failure polarity. ``strict_walk`` raises
    ``TreeVanishedError`` because a silently-omitted entry there is an
    integrity bug; here a vanished entry is the expected outcome of a
    concurrent sweep and is elided.

    Per-entry ``FileNotFoundError``/``NotADirectoryError``
    (:data:`VANISHED_ERRORS`) elide that entry. Every other ``OSError`` --
    ``PermissionError`` in particular -- propagates.

    *root* itself is opened eagerly, so a missing or non-directory root
    raises from this call, **not** from first iteration. That distinction is
    load-bearing: "the directory is gone" and "an entry in it vanished" are
    different events and must not collapse into one.

    Non-recursive and does not follow symlinks (``lstat`` semantics).
    """
    return _ObservedScan(os.scandir(root))


class _ScandirHandle(Protocol):
    def __next__(self) -> os.DirEntry[str]: ...

    def close(self) -> None: ...


class _ObservedScan(Iterator[ObservedEntry]):
    def __init__(self, scanner: _ScandirHandle) -> None:
        self._scanner = scanner
        self._closed = False

    def __next__(self) -> ObservedEntry:
        while True:
            try:
                entry = next(self._scanner)
            except StopIteration:
                self.close()
                raise
            except BaseException:
                self.close()
                raise
            try:
                status = entry.stat(follow_symlinks=False)
            except VANISHED_ERRORS:
                continue
            except BaseException:
                self.close()
                raise
            return ObservedEntry(path=Path(entry.path), status=status)

    def close(self) -> None:
        if not self._closed:
            self._scanner.close()
            self._closed = True

    def __del__(self) -> None:
        self.close()


def observe_path_mode(path: Path) -> int | None:
    """Return ``st_mode`` from ``lstat``, or ``None`` when the path no longer resolves."""
    try:
        return path.lstat().st_mode
    except VANISHED_ERRORS:
        return None


def safe_mtime(path: Path) -> float | None:
    """Return ``st_mtime``, or ``None`` when the path no longer resolves."""
    try:
        return os.path.getmtime(path)
    except VANISHED_ERRORS:
        return None

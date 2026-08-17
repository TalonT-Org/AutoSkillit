"""Record I/O helpers for the managed headless session lineage store.

Extracted from `_managed_headless_session_lineage.py`. These helpers
own the per-record creation-projection, anchor resolution, root
preparation, file-locked store context, record path resolution, and
read/write of one lineage record.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from autoskillit.core import (
    MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    NativeShellCaptureDecision,
    atomic_write,
)
from autoskillit.execution.session._managed_headless_session_lineage import (
    _DISPATCH_INDEX,
    _FINAL_NATIVE_INDEX,
    _INDEXES_DIR,
    _LOCK_FILENAME,
    _MAX_RECORD_BYTES,
    _NAMESPACE,
    _RECORDS_DIR,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    canonical_json as _canonical_json,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    digest as _digest,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    lineage_from_dict as _lineage_from_dict,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    record_payload as _record_payload,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    record_to_dict as _record_to_dict,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    strict_json_load as _strict_json_load,
)


def _new_lineage(
    *,
    launch_id: str,
    decision: NativeShellCaptureDecision,
    backend: str,
    session_kind: ManagedHeadlessSessionKind,
    lineage_anchor: Path,
    anchor_device: int,
    anchor_inode: int,
    dispatch_id: str | None,
) -> ManagedHeadlessSessionLineage:
    identity = {
        "schema_version": MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
        "launch_id": launch_id,
        "decision": decision.to_dict(),
        "backend": backend,
        "session_kind": session_kind.value,
        "lineage_anchor": str(lineage_anchor),
        "anchor_device": anchor_device,
        "anchor_inode": anchor_inode,
    }
    lineage_digest = _digest(identity)
    provisional = ManagedHeadlessSessionLineage(
        launch_id=launch_id,
        decision=decision,
        backend=backend,
        session_kind=session_kind,
        lineage_anchor=str(lineage_anchor),
        anchor_device=anchor_device,
        anchor_inode=anchor_inode,
        lineage_digest=lineage_digest,
        generation=0,
        record_digest="0" * 64,
        dispatch_id=dispatch_id,
    )
    return replace(provisional, record_digest=_digest(_record_payload(provisional)))


def _next_generation(
    lineage: ManagedHeadlessSessionLineage,
) -> ManagedHeadlessSessionLineage:
    provisional = replace(
        lineage,
        generation=lineage.generation + 1,
        record_digest="0" * 64,
    )
    return replace(provisional, record_digest=_digest(_record_payload(provisional)))


def _creation_projection(lineage: ManagedHeadlessSessionLineage) -> tuple[object, ...]:
    return (
        lineage.launch_id,
        lineage.decision,
        lineage.backend,
        lineage.session_kind,
        lineage.lineage_anchor,
        lineage.anchor_device,
        lineage.anchor_inode,
        lineage.dispatch_id,
    )


def _resolve_anchor(lineage_anchor: Path) -> tuple[Path, int, int]:
    supplied = Path(lineage_anchor).expanduser()
    if not supplied.is_absolute():
        raise ValueError("Managed lineage anchor must be absolute")
    try:
        anchor = supplied.resolve(strict=True)
        stat_result = anchor.stat()
    except OSError as exc:
        raise ValueError("Managed lineage anchor is unavailable") from exc
    if not anchor.is_dir():
        raise ValueError("Managed lineage anchor must be a directory")
    return anchor, stat_result.st_dev, stat_result.st_ino


def _prepare_root(anchor: Path) -> Path:
    current = anchor
    for component in _NAMESPACE.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ValueError("Managed lineage namespace cannot contain symlinks")
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("Managed lineage namespace is not a regular directory")
    for relative in (
        Path(_RECORDS_DIR),
        Path(_INDEXES_DIR) / _FINAL_NATIVE_INDEX,
        Path(_INDEXES_DIR) / _DISPATCH_INDEX,
    ):
        directory = current / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Managed lineage namespace is not a regular directory")
    return current


@contextmanager
def _store_lock(root: Path, *, exclusive: bool) -> Iterator[None]:
    lock_path = root / _LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _record_path(root: Path, launch_id: str) -> Path:
    # Construction validates the identity before this path can be written.
    if (
        not isinstance(launch_id, str)
        or len(launch_id) != 32
        or any(character not in "0123456789abcdef" for character in launch_id)
    ):
        raise ValueError("Invalid launch_id")
    return root / _RECORDS_DIR / f"{launch_id}.json"


def _write_record(path: Path, lineage: ManagedHeadlessSessionLineage) -> None:
    atomic_write(
        path,
        _canonical_json(_record_to_dict(lineage)),
        strict_durability=True,
    )


def _read_record(path: Path) -> ManagedHeadlessSessionLineage:
    raw = _read_bounded(path)
    value = _strict_json_load(raw)
    if _canonical_json(value).encode("utf-8") != raw:
        raise ValueError("Managed lineage record is not canonical JSON")
    return _lineage_from_dict(value)


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        raise FileNotFoundError(f"Managed lineage record not found: {path.name}") from None
    if len(raw) > _MAX_RECORD_BYTES:
        raise ValueError("Managed lineage artifact is oversized")
    return raw

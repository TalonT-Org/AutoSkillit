"""Clone cleanup registry — shared file-based coordination for deferred batch cleanup.

Parallel pipeline instances write their clone path + completion status here.
After all pipelines complete, batch_cleanup_clones reads the registry and
removes only success-status clones.

Registry file format:
    {"clones": [{"path": "/abs/path", "status": "success|error", "owner": "kitchen-uuid"}]}
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, Literal

from autoskillit.core import atomic_write, get_logger, resolve_temp_dir

_log = get_logger(__name__)

CloneStatus = Literal["success", "error"]
_DEFAULT_REGISTRY_NAME = "clone-cleanup-registry.json"


def _resolve_registry_path(registry_path: str, temp_dir: Path | None = None) -> Path:
    if registry_path:
        return Path(registry_path)
    base = temp_dir if temp_dir is not None else resolve_temp_dir(Path.cwd(), None)
    return base / _DEFAULT_REGISTRY_NAME


def _entry_matches_owner(entry: dict[str, str], owner: str | None) -> bool:
    if owner is None:
        return True
    return entry.get("owner") == owner


class CloneRegistry:
    """Locked context manager for clone-cleanup-registry.json.

    Acquires fcntl.LOCK_EX on __enter__, reads _entries from disk.
    Writes _entries back atomically on __exit__ iff any mutation occurred.
    Releases the lock unconditionally on __exit__ (even on exception).

    All mutation methods set _dirty = True so __exit__ knows to persist.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(".lock")
        self._entries: list[dict[str, str]] = []
        self._dirty: bool = False
        self._lock_file: IO[str] | None = None

    def __enter__(self) -> CloneRegistry:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self._lock_path, "w")
        fcntl.flock(self._lock_file, fcntl.LOCK_EX)
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text()).get("clones", [])
            except (json.JSONDecodeError, OSError) as exc:
                _log.warning("clone_registry: could not read %s: %s", self._path, exc)
                self._entries = []
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        try:
            if self._dirty:
                atomic_write(
                    self._path,
                    json.dumps({"clones": self._entries}, indent=2),
                )
        finally:
            if self._lock_file is not None:
                self._lock_file.close()  # releases fcntl advisory lock

    def append(self, path: str, status: str, owner: str) -> None:
        self._entries.append({"path": path, "status": status, "owner": owner})
        self._dirty = True

    def candidates(self, owner: str | None) -> tuple[list[str], list[str]]:
        """Return (to_delete, to_preserve) paths scoped to owner.

        to_delete: status=success entries matching owner filter.
        to_preserve: status=error entries matching owner filter.
        Entries belonging to other owners are not reported in either list.
        """
        to_delete: list[str] = []
        to_preserve: list[str] = []
        for entry in self._entries:
            if owner is not None and entry.get("owner") != owner:
                continue
            path = entry.get("path")
            if path is None:
                continue
            if entry.get("status") == "success":
                to_delete.append(path)
            else:
                to_preserve.append(path)
        return to_delete, to_preserve

    def prune_deleted(self, deleted_paths: set[str]) -> None:
        """Remove successfully-deleted entries from the in-memory list.

        Entries not in deleted_paths (failed deletes, other owners) are retained.
        Sets _dirty = True so __exit__ persists the pruned list.
        """
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["path"] not in deleted_paths]
        if len(self._entries) < before:
            self._dirty = True


def register_clone(
    clone_path: str,
    status: CloneStatus,
    owner: str,
    registry_path: str = "",
    temp_dir: Path | None = None,
) -> dict[str, str]:
    """Append a clone entry to the registry. Safe for parallel callers — holds an
    exclusive advisory lock across the entire read-modify-write sequence."""
    if owner == "":
        raise ValueError("owner is required")
    path = _resolve_registry_path(registry_path, temp_dir)
    with CloneRegistry(path) as reg:
        reg.append(clone_path, status, owner)
    return {"registered": "true", "registry_path": str(path)}


def read_registry(
    registry_path: str = "",
    temp_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Return all registry entries, or [] if the file does not exist."""
    path = _resolve_registry_path(registry_path, temp_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("clones", [])
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("clone_registry: could not read %s: %s", path, exc)
        return []


def cleanup_candidates(
    registry_path: str = "",
    temp_dir: Path | None = None,
    owner: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return (to_delete, to_preserve) path lists from the registry.

    to_delete  — clones with status='success' (safe to remove)
    to_preserve — clones with status='error'  (preserve for investigation)

    When owner is provided, only entries belonging to that owner are considered.
    Entries without an 'owner' field (legacy orphans) are only visible when
    owner is None (all-owners mode).
    """
    entries = read_registry(registry_path, temp_dir)
    to_delete = [
        e["path"]
        for e in entries
        if e.get("status") == "success" and "path" in e and _entry_matches_owner(e, owner)
    ]
    to_preserve = [
        e["path"]
        for e in entries
        if e.get("status") == "error" and "path" in e and _entry_matches_owner(e, owner)
    ]
    return to_delete, to_preserve


def batch_delete(
    registry_path: str,
    remove_fn: Callable[[str, str], dict[str, str]],
    temp_dir: Path | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Read registry and delete success-status clones via remove_fn, then prune registry.

    Three-phase sequence:
    1. Read candidates under lock (short hold — no I/O inside).
    2. Delete outside lock (slow filesystem I/O).
    3. Prune succeeded entries under lock (re-reads fresh, capturing concurrent appends).

    Calls remove_fn(path, "false") for each success clone. Error clones are
    preserved. Returns {"deleted": [...], "delete_failures": [...], "preserved": [...]}.

    When owner is provided, only entries belonging to that owner are considered.
    Pass owner=None to operate on all entries (escape hatch mode).
    """
    path = _resolve_registry_path(registry_path, temp_dir)

    # Phase 1: read candidates under lock (short hold — no I/O inside)
    with CloneRegistry(path) as reg:
        to_delete, to_preserve = reg.candidates(owner)

    # Phase 2: delete outside lock (slow filesystem I/O)
    deleted: list[str] = []
    delete_failures: list[dict[str, str]] = []
    for clone_path in to_delete:
        result = remove_fn(clone_path, "false")
        if result.get("removed") == "true":
            deleted.append(clone_path)
        else:
            delete_failures.append({"path": clone_path, "reason": result.get("reason", "unknown")})

    # Phase 3: prune successfully-deleted entries (re-reads fresh under lock,
    # capturing any register_clone writes that arrived during Phase 2)
    if deleted:
        with CloneRegistry(path) as reg:
            reg.prune_deleted(set(deleted))
            _, to_preserve = reg.candidates(owner)

    return {"deleted": deleted, "delete_failures": delete_failures, "preserved": to_preserve}

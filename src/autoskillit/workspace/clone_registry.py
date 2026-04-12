"""Clone cleanup registry — shared file-based coordination for deferred batch cleanup.

Parallel pipeline instances write their clone path + completion status here.
After all pipelines complete, batch_cleanup_clones reads the registry and
removes only success-status clones.

Registry file format:
    {"clones": [{"path": "/abs/path/to/clone", "status": "success|error", "owner": "kitchen-uuid"}]}
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        existing: list[dict[str, str]] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text()).get("clones", [])
            except (json.JSONDecodeError, OSError) as exc:
                _log.warning("clone_registry: could not read %s: %s", path, exc)
        existing.append({"path": clone_path, "status": status, "owner": owner})
        atomic_write(path, json.dumps({"clones": existing}, indent=2))
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
    """Read registry and delete success-status clones via remove_fn.

    Calls remove_fn(path, "false") for each success clone. Error clones are
    preserved. Returns {"deleted": [...], "delete_failures": [...], "preserved": [...]}.

    When owner is provided, only entries belonging to that owner are considered.
    Pass owner=None to operate on all entries (escape hatch mode).
    """
    to_delete, to_preserve = cleanup_candidates(registry_path, temp_dir, owner=owner)
    deleted: list[str] = []
    delete_failures: list[dict[str, str]] = []
    for path in to_delete:
        result = remove_fn(path, "false")
        if result.get("removed") == "true":
            deleted.append(path)
        else:
            delete_failures.append({"path": path, "reason": result.get("reason", "unknown")})
    return {"deleted": deleted, "delete_failures": delete_failures, "preserved": to_preserve}

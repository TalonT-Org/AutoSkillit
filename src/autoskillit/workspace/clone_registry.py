"""Clone cleanup registry — shared file-based coordination for deferred batch cleanup.

Parallel pipeline instances write their clone path + completion status here.
After all pipelines complete, batch_cleanup_clones reads the registry and
removes only success-status clones.

Registry file format:
    {"clones": [{"path": "/abs/path/to/clone", "status": "success|error"}]}
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from autoskillit.core import atomic_write, get_logger

_log = get_logger(__name__)

CloneStatus = Literal["success", "error"]
_DEFAULT_REGISTRY_NAME = "clone-cleanup-registry.json"


def _resolve_registry_path(registry_path: str) -> Path:
    if registry_path:
        return Path(registry_path)
    # Default location mirrors the project temp convention. atomic_write creates
    # parent dirs; no need to call ensure_project_temp (which requires project_dir).
    return Path.cwd() / ".autoskillit" / "temp" / _DEFAULT_REGISTRY_NAME


def register_clone(
    clone_path: str,
    status: CloneStatus,
    registry_path: str = "",
) -> dict[str, str]:
    """Append a clone entry to the registry. Safe for parallel callers — holds an
    exclusive advisory lock across the entire read-modify-write sequence."""
    path = _resolve_registry_path(registry_path)
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
        existing.append({"path": clone_path, "status": status})
        atomic_write(path, json.dumps({"clones": existing}, indent=2))
    return {"registered": "true", "registry_path": str(path)}


def read_registry(registry_path: str = "") -> list[dict[str, str]]:
    """Return all registry entries, or [] if the file does not exist."""
    path = _resolve_registry_path(registry_path)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("clones", [])
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("clone_registry: could not read %s: %s", path, exc)
        return []


def cleanup_candidates(
    registry_path: str = "",
) -> tuple[list[str], list[str]]:
    """Return (to_delete, to_preserve) path lists from the registry.

    to_delete  — clones with status='success' (safe to remove)
    to_preserve — clones with status='error'  (preserve for investigation)
    """
    entries = read_registry(registry_path)
    to_delete = [e["path"] for e in entries if e.get("status") == "success" and "path" in e]
    to_preserve = [e["path"] for e in entries if e.get("status") == "error" and "path" in e]
    return to_delete, to_preserve


def batch_delete(
    registry_path: str,
    remove_fn: Callable[[str, str], dict[str, str]],
) -> dict[str, Any]:
    """Read registry and delete success-status clones via remove_fn.

    Calls remove_fn(path, "false") for each success clone. Error clones are
    preserved. Returns {"deleted": [...], "delete_failures": [...], "preserved": [...]}.
    """
    to_delete, to_preserve = cleanup_candidates(registry_path)
    deleted: list[str] = []
    delete_failures: list[dict[str, str]] = []
    for path in to_delete:
        result = remove_fn(path, "false")
        if result.get("removed") == "true":
            deleted.append(path)
        else:
            delete_failures.append({"path": path, "reason": result.get("reason", "unknown")})
    return {"deleted": deleted, "delete_failures": delete_failures, "preserved": to_preserve}

"""Clone cleanup registry — shared file-based coordination for deferred batch cleanup.

Parallel pipeline instances write their clone path + completion status here.
After all pipelines complete, batch_cleanup_clones reads the registry and
removes only success-status clones.

Registry file format:
    {"clones": [{"path": "/abs/path/to/clone", "status": "success|error"}]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from autoskillit.core.io import atomic_write
from autoskillit.core.logging import get_logger

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
    """Append a clone entry to the registry. Atomic write — safe for parallel callers."""
    path = _resolve_registry_path(registry_path)
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
    to_delete = [e["path"] for e in entries if e.get("status") == "success"]
    to_preserve = [e["path"] for e in entries if e.get("status") == "error"]
    return to_delete, to_preserve

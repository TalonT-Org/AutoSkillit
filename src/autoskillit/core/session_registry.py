"""Session registry: maps autoskillit launch IDs to Claude Code session UUIDs.

Written at interactive session launch; bridged on open_kitchen hook fire.
Read by the scoped resume picker to classify sessions by type.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "registry_path",
    "read_registry",
    "write_registry_entry",
    "bridge_claude_session_id",
]


def registry_path(project_dir: Path) -> Path:
    """Return .autoskillit/temp/session_registry.json path."""
    return project_dir / ".autoskillit" / "temp" / "session_registry.json"


def read_registry(project_dir: Path) -> dict[str, dict]:
    """Read registry. Returns {} on missing file or malformed JSON."""
    path = registry_path(project_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_registry_entry(
    project_dir: Path,
    launch_id: str,
    session_type: str,
    recipe_name: str | None,
) -> None:
    """Atomically add/update entry keyed by launch_id.

    Fields: session_type, launched_at (ISO8601), recipe_name, claude_session_id (null).
    Merges with existing registry (does not clobber unrelated entries).
    """
    path = registry_path(project_dir)
    try:
        existing: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}

    existing[launch_id] = {
        "session_type": session_type,
        "launched_at": datetime.now(UTC).isoformat(),
        "recipe_name": recipe_name,
        "claude_session_id": None,
    }

    _atomic_write(path, json.dumps(existing))


def bridge_claude_session_id(
    project_dir: Path,
    launch_id: str,
    claude_session_id: str,
) -> None:
    """Update entry for launch_id with the Claude Code session UUID.

    No-op if launch_id not found. Uses atomic write.
    """
    path = registry_path(project_dir)
    try:
        registry: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if launch_id not in registry:
        return

    registry[launch_id]["claude_session_id"] = claude_session_id
    _atomic_write(path, json.dumps(registry))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

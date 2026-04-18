"""Kitchen-open session marker — stdlib-only, importable from hook subprocesses.

Provides disk-based session state so PreToolUse hooks can detect whether
open_kitchen has been called in the current session. Hook subprocesses are
fresh-spawned on each invocation and share no memory — a disk marker is the
only cross-process handshake surface.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class KitchenMarker:
    """Immutable record written when open_kitchen is called."""

    session_id: str
    opened_at: datetime
    recipe_name: str | None
    marker_version: int = 1
    content_hash: str = ""
    composite_hash: str = ""


def get_state_dir() -> Path:
    """Return the kitchen-state directory.

    Reads AUTOSKILLIT_STATE_DIR env (for test isolation); falls back to the
    canonical temp location derived from Path.cwd().
    """
    override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    if override:
        return Path(override) / "kitchen_state"
    # Canonical default: .autoskillit/temp/kitchen_state relative to CWD
    return Path.cwd() / ".autoskillit" / "temp" / "kitchen_state"


def marker_path(session_id: str) -> Path:
    """Return the Path where the marker for session_id is stored."""
    return get_state_dir() / f"{session_id}.json"


def write_marker(
    session_id: str,
    recipe_name: str | None,
    *,
    content_hash: str = "",
    composite_hash: str = "",
) -> None:
    """Atomically write a kitchen-open marker for session_id.

    Uses stdlib tempfile + os.replace for crash-safety.
    """
    import tempfile

    path = marker_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "opened_at": datetime.now(UTC).isoformat(),
        "recipe_name": recipe_name,
        "marker_version": 1,
        "content_hash": content_hash,
        "composite_hash": composite_hash,
    }
    content = json.dumps(payload)
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


def read_marker(session_id: str) -> KitchenMarker | None:
    """Read and validate the kitchen marker for session_id.

    Returns None on missing file, malformed JSON, or schema mismatch.
    """
    path = marker_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return KitchenMarker(
            session_id=data["session_id"],
            opened_at=datetime.fromisoformat(data["opened_at"]),
            recipe_name=data.get("recipe_name"),
            marker_version=data.get("marker_version", 1),
            content_hash=data.get("content_hash", ""),
            composite_hash=data.get("composite_hash", ""),
        )
    except (KeyError, ValueError, TypeError):
        return None


def is_marker_fresh(marker: KitchenMarker, ttl_hours: int = 24) -> bool:
    """Return True if the marker is within the TTL window."""
    age = datetime.now(UTC) - marker.opened_at
    return age.total_seconds() < ttl_hours * 3600


def sweep_stale_markers(ttl_hours: int = 24) -> int:
    """Delete all markers older than ttl_hours. Returns count of deleted markers."""
    state_dir = get_state_dir()
    if not state_dir.is_dir():
        return 0
    deleted = 0
    for p in state_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            opened_at = datetime.fromisoformat(data["opened_at"])
            age = datetime.now(UTC) - opened_at
            if age.total_seconds() >= ttl_hours * 3600:
                p.unlink()
                deleted += 1
        except (OSError, json.JSONDecodeError, KeyError, ValueError, UnicodeDecodeError):
            # Malformed or unreadable markers are treated as stale and removed
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted

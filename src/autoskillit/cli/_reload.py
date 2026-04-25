"""Reload sentinel detection for interactive session re-launch loops."""

from __future__ import annotations

import json
from pathlib import Path


def _reload_sentinel_dir(project_dir: Path) -> Path:
    return project_dir / ".autoskillit" / "temp" / "reload_sentinel"


def consume_reload_sentinel(project_dir: Path) -> str | None:
    """Scan for a reload sentinel file; if found, consume and return session_id."""
    sentinel_dir = _reload_sentinel_dir(project_dir)
    if not sentinel_dir.is_dir():
        return None
    candidates = sorted(sentinel_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    for stale in candidates[1:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    sentinel = candidates[0]
    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
        session_id = data.get("session_id", "")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    try:
        sentinel.unlink(missing_ok=True)
    except OSError:
        pass
    return session_id or None

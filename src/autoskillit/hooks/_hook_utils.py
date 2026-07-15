"""Shared stdlib-only utilities for hook scripts.

Imported by both PreToolUse guards (in guards/) and PostToolUse hooks
via sys.path bootstrap.  Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

from pathlib import Path


def find_project_root() -> Path:
    """Walk up from CWD to find nearest ancestor containing .autoskillit/."""
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / ".autoskillit").is_dir():
            return ancestor
    return cwd


def discover_single_tracker_order_id(tracker_dir: Path) -> str:
    """Return the order_id of the sole tracker file in tracker_dir, or "" if not exactly one."""
    if not tracker_dir.is_dir():
        return ""
    trackers = [
        f for f in tracker_dir.iterdir() if f.suffix == ".json" and not f.name.startswith(".")
    ]
    if len(trackers) == 1:
        return trackers[0].stem
    return ""

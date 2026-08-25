"""Reload sentinel detection for interactive session re-launch loops."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autoskillit.core import get_logger, safe_mtime

logger = get_logger(__name__)


def _reload_sentinel_dir(project_dir: Path) -> Path:
    return project_dir / ".autoskillit" / "temp" / "reload_sentinel"


@contextmanager
def _reload_lock(sentinel_dir: Path) -> Iterator[None]:
    """Serialize consume_reload_sentinel callers against a shared sentinel_dir.

    Two independent OS processes (cook, launch) poll the same reload_sentinel/
    directory; without this, their enumerate/prune/read/delete sequences can
    interleave and race. Mirrors server/_recipe_artifact.py's _generation_lock
    (a fixed-name lock file created inside the locked directory) — the closer
    precedent for locking a directory, versus core/runtime/session_registry.py's
    _registry_lock, which locks a file's sibling instead.
    """
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    with (sentinel_dir / ".lock").open("a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def consume_reload_sentinel(project_dir: Path) -> str | None:
    """Scan for a reload sentinel file; if found, consume and return session_id."""
    sentinel_dir = _reload_sentinel_dir(project_dir)
    if not sentinel_dir.is_dir():
        return None
    with _reload_lock(sentinel_dir):
        candidates = sorted(
            sentinel_dir.glob("*.json"), key=lambda p: safe_mtime(p) or 0.0, reverse=True
        )
        if not candidates:
            return None
        for stale in candidates[1:]:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                logger.warning("reload_sentinel_cleanup_failed", path=str(stale), exc_info=True)
                return None
        sentinel = candidates[0]
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            session_id = data.get("session_id", "")
        except (OSError, json.JSONDecodeError):
            return None
        try:
            sentinel.unlink(missing_ok=True)
        except OSError:
            logger.warning("reload_sentinel_cleanup_failed", path=str(sentinel), exc_info=True)
            return None
        return session_id or None

"""Session-log directory retention — apply_session_retention().

Extracted out of session_log.py's flush_session_log() (S2-2) so
tests/_retention_surface.py's reclamation retention-decision registry can target
this exact unit, rather than the ~600 unrelated lines around it in
flush_session_log, and so session_log.py stays under its 750-line warning-zone
budget (tests/arch/test_file_size_budgets.py).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import VANISHED_ERRORS, atomic_write, get_logger, scan_observed

logger = get_logger(__name__)

_MAX_SESSIONS = 2000
_CLEAR_MARKER_FILENAME = ".telemetry_cleared_at"


def write_telemetry_clear_marker(log_root: Path) -> None:
    """Write the current UTC timestamp as a telemetry-clear fence."""
    try:
        log_root = Path(log_root)
        log_root.mkdir(parents=True, exist_ok=True)
        atomic_write(log_root / _CLEAR_MARKER_FILENAME, datetime.now(UTC).isoformat())
    except (OSError, ValueError, TypeError) as exc:
        # Narrow catch for filesystem ops and atomic_write's known exception set;
        # broader ``Exception`` would mask programmatic bugs (AttributeError, KeyError)
        # as routine retention failures.
        logger.debug(
            "write_telemetry_clear_marker failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


def read_telemetry_clear_marker(log_root: Path) -> datetime | None:
    """Read the persisted telemetry-clear timestamp, or None if absent/corrupt."""
    try:
        text = (Path(log_root) / _CLEAR_MARKER_FILENAME).read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(text)
    except (OSError, ValueError):
        return None


def apply_session_retention(
    sessions_dir: Path,
    *,
    max_sessions: int | None,
    dir_name: str,
    reuse_committed_recovery: bool,
    protected_ids: frozenset[str],
) -> set[str]:
    """Delete committed session dirs beyond the retention window; return survivors.

    A survivor is either not expired, or expired but protected (the just-
    recommitted crash-recovery dir, a campaign-protected id, or a dir whose
    deletion itself failed).
    """
    committed_dirs = sorted(
        (
            entry
            for entry in scan_observed(sessions_dir)
            if entry.is_dir and (entry.path / "summary.json").is_file()
        ),
        key=lambda entry: entry.status.st_mtime_ns,
    )
    effective_max_sessions = max_sessions if max_sessions is not None else _MAX_SESSIONS
    expired = committed_dirs[: max(0, len(committed_dirs) - effective_max_sessions)]
    surviving_names = {entry.name for entry in committed_dirs[len(expired) :]}
    for entry in expired:
        if reuse_committed_recovery and entry.name == dir_name:
            surviving_names.add(entry.name)
            continue
        if protected_ids:
            try:
                meta = json.loads((entry.path / "meta.json").read_text(encoding="utf-8"))
            except VANISHED_ERRORS:
                meta = {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "session_retention_meta_read_failed",
                    path=entry.path,
                    error=str(exc),
                    exc_info=True,
                )
                meta = {}
            if meta.get("campaign_id") in protected_ids:
                surviving_names.add(entry.name)
                continue
        try:
            shutil.rmtree(entry.path)
        except OSError:
            logger.warning("session_retention_delete_failed", path=entry.path, exc_info=True)
            surviving_names.add(entry.name)
    return surviving_names

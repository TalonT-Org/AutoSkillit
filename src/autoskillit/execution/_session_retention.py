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
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)

_MAX_SESSIONS = 2000


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
            candidate
            for candidate in sessions_dir.iterdir()
            if candidate.is_dir() and (candidate / "summary.json").is_file()
        ),
        key=lambda candidate: candidate.stat().st_mtime,
    )
    effective_max_sessions = max_sessions if max_sessions is not None else _MAX_SESSIONS
    expired = committed_dirs[: max(0, len(committed_dirs) - effective_max_sessions)]
    surviving_names = {candidate.name for candidate in committed_dirs[len(expired) :]}
    for candidate in expired:
        if reuse_committed_recovery and candidate.name == dir_name:
            surviving_names.add(candidate.name)
            continue
        if protected_ids:
            try:
                meta = json.loads((candidate / "meta.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                meta = {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "session_retention_meta_read_failed",
                    path=candidate,
                    error=str(exc),
                    exc_info=True,
                )
                meta = {}
            if meta.get("campaign_id") in protected_ids:
                surviving_names.add(candidate.name)
                continue
        try:
            shutil.rmtree(candidate)
        except OSError:
            logger.warning("session_retention_delete_failed", path=candidate, exc_info=True)
            surviving_names.add(candidate.name)
    return surviving_names

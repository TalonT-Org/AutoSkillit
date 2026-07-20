"""Stdlib-only stale-capture-file sweep helpers (used by ``session_start_hook.py``).

Reaps capture artifacts written by ``shell_capture_hook.py`` after their
capture-session lifetime has elapsed. Mirrors the TTL-sweep pattern in
``session_start_hook.py`` (kitchen_state, pipeline_tracker) with containment
checks inlined from ``core.path_containment.resolve_contained_path`` —
the stdlib-only hook import boundary prevents importing from ``autoskillit.*``.

The 1-hour age threshold (``max_age_seconds=3600`` default) is the liveness
guard: a capture file currently being written by an in-flight harness has an
``mtime`` within seconds of now and will never be swept. POSIX ``unlink`` on
an open fd is safe (the inode persists until fd close), so even in the
pathological case of a sweep racing with an active reader, no data
corruption occurs — only premature path removal, which the age threshold
makes effectively impossible.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

__all__ = ["_CAPTURE_FILENAME_RE", "_is_safe_capture_file", "sweep_stale_captures"]

# Strict allowlist matching the 16-hex-char uid produced by
# ``shell_capture_hook._build_harness`` (``uuid4().hex[:16]``).
_CAPTURE_FILENAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")


def _is_safe_capture_file(path: Path, capture_dir: Path) -> bool:
    """Return True iff ``path`` is a safe, in-place, regular capture file.

    Rejects:
    - Names that don't match the strict ``shell_<16hex>.log`` pattern
    - Symlinks (``stat.S_ISLNK``)
    - Paths whose resolved target escapes ``capture_dir``
    - Hardlinks (``st_nlink > 1``)
    - World-writable files (``st_mode & 0o002``)
    """
    if not _CAPTURE_FILENAME_RE.match(path.name):
        return False
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return False
    if st.st_nlink > 1:
        return False
    if st.st_mode & 0o002:
        return False
    try:
        capture_dir_resolved = Path(capture_dir).resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_relative_to(capture_dir_resolved):
        return False
    return True


def sweep_stale_captures(
    capture_dir: Path | str,
    *,
    max_age_seconds: int = 3600,
) -> int:
    """Delete capture files in ``capture_dir`` older than ``max_age_seconds``.

    Returns the count of deleted files. Fail-open: any per-file exception
    is swallowed and the iteration continues, mirroring ``session_start_hook.py``
    TTL-sweep behavior.
    """
    capture_dir_path = Path(capture_dir)
    if not capture_dir_path.is_dir():
        return 0
    try:
        capture_dir_resolved = capture_dir_path.resolve(strict=True)
        now_mtime_threshold = capture_dir_resolved.stat().st_mtime - max_age_seconds
    except OSError:
        return 0

    deleted = 0
    try:
        entries = list(capture_dir_path.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if not _is_safe_capture_file(entry, capture_dir_path):
                continue
            st = entry.stat()
            if st.st_mtime > now_mtime_threshold:
                continue
            os.unlink(entry)
            deleted += 1
        except (FileNotFoundError, OSError):
            continue
    return deleted

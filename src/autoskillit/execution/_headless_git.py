"""Git helpers for headless session LOC tracking.

Extracted from headless.py to keep that module below the architectural line budget.
IL-1 module (execution/).
"""

from __future__ import annotations

import subprocess

from autoskillit.core import get_logger

logger = get_logger(__name__)


def _capture_git_head_sha(cwd: str) -> str:
    """Return current HEAD SHA in cwd. Returns '' on any error (non-git dirs)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        logger.debug("capture_git_head_sha_failed", cwd=cwd, exc_info=True)
        return ""


def _parse_numstat(numstat_output: str) -> tuple[int, int]:
    """Parse `git diff --numstat` output into (insertions, deletions).

    Binary file lines (-\\t-\\tfilename) are skipped.
    """
    insertions = deletions = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        try:
            insertions += int(parts[0])
            deletions += int(parts[1])
        except ValueError:
            continue  # binary file row: "-\t-\tfilename"
    return insertions, deletions


def _compute_loc_changed(cwd: str, pre_sha: str) -> tuple[int, int]:
    """Run git diff --numstat <pre_sha> in cwd. Returns (0, 0) on any error."""
    if not pre_sha:
        return 0, 0
    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", pre_sha],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return 0, 0
        return _parse_numstat(result.stdout)
    except Exception:
        logger.debug("compute_loc_changed_failed", cwd=cwd, pre_sha=pre_sha, exc_info=True)
        return 0, 0

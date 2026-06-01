"""Git helpers for headless session LOC tracking.

Extracted from headless.py to keep that module below the architectural line budget.
IL-1 module (execution/).
"""

from __future__ import annotations

import subprocess

from autoskillit.core import get_logger, resolve_clone_remote_name_sync

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


def _detect_session_git_writes(cwd: str, pre_session_sha: str) -> bool:
    """Return True iff the session committed new changes to the repo.

    Compares pre-session HEAD SHA to post-session HEAD SHA; if they differ
    the session made commits. Returns False when pre_session_sha is empty
    (non-git dir or capture error at session start — safe default).
    """
    if not pre_session_sha:
        return False
    post_sha = _capture_git_head_sha(cwd)
    if not post_sha:
        return False
    return post_sha != pre_session_sha


def _detect_branch_divergence(cwd: str) -> bool:
    """Check if current branch has commits ahead of the remote default branch.

    Tries origin/HEAD, origin/main, origin/master as base references.
    Returns the result from the first ref that resolves successfully;
    remaining refs are only tried when merge-base or rev-list fails.
    Returns False on any error or non-git directory.
    """
    remote = resolve_clone_remote_name_sync(cwd)
    for ref in (f"{remote}/HEAD", f"{remote}/main", f"{remote}/master"):
        try:
            mb = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if mb.returncode != 0:
                continue
            merge_base_sha = mb.stdout.strip()
            if not merge_base_sha:
                continue

            rl = subprocess.run(
                ["git", "rev-list", "--count", f"{merge_base_sha}..HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if rl.returncode == 0:
                raw = rl.stdout.strip()
                if not raw:
                    continue
                try:
                    count = int(raw)
                except ValueError:
                    continue
                return count > 0
        except Exception:
            logger.debug(
                "detect_branch_divergence_failed",
                cwd=cwd,
                ref=ref,
                exc_info=True,
            )
            continue

    return False

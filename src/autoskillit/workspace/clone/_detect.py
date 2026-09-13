"""Source directory detection helpers and remote URL classification."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)

RUNS_DIR = "autoskillit-runs"

# URL prefixes that unambiguously identify a network remote
_NETWORK_URL_PREFIXES = ("https://", "http://", "git@", "git://", "ssh://", "file://")


def _is_not_file_url(url: str) -> bool:
    """Return True if url is a usable clone source (not a self-referential file:// URL).

    file:// URLs are written by _ensure_origin_isolated to point at the clone directory
    itself. Using them as a clone source would clone from the stale local working tree
    instead of fetching fresh state from the real remote (the #817 bug).

    Local bare paths (no scheme) and real network URLs (https://, git@, etc.) are both
    valid clone sources and are accepted.
    """
    return bool(url) and not url.startswith("file://")


def classify_remote_url(url: str) -> str:
    """Classify a git remote URL as 'network', 'bare_local', 'nonbare_local', 'none', or 'unknown'.

    Network URLs: https://, http://, git@, git://, ssh://, file:// scheme.
    Bare local: a local filesystem path where git rev-parse --is-bare-repository returns true.
    Nonbare local: a local filesystem path that is a non-bare git repo.
    None: empty string (no remote configured).
    Unknown: local path that does not exist or is not a git repo.
    """
    if not url:
        return "none"
    if any(url.startswith(prefix) for prefix in _NETWORK_URL_PREFIXES):
        return "network"
    # Treat as a local filesystem path
    path = Path(url).expanduser().resolve()
    if not path.exists():
        return "unknown"
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-bare-repository"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "unknown"
    return "bare_local" if result.stdout.strip() == "true" else "nonbare_local"


def detect_source_dir(cwd: str) -> str:
    """Detect the git repository root for cwd, falling back to cwd.

    Shells 'git rev-parse --show-toplevel' from cwd. Returns cwd unchanged
    if not in a git repository or if git is not available.
    """
    _cwd = cwd if cwd else str(Path.cwd())
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=_cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return _cwd


def detect_branch(source_dir: str) -> str:
    """Detect the current HEAD branch in source_dir.

    Returns the branch name on success. Returns "" on git failure.
    Returns the literal "HEAD" when the repo is in detached HEAD state;
    callers must treat "HEAD" as no usable branch name.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def detect_uncommitted_changes(source_dir: str) -> list[str]:
    """Return porcelain status lines when uncommitted changes exist.

    Returns empty list when the working tree is clean or when git is
    unavailable. Never raises.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return [line for line in result.stdout.splitlines() if line.strip()]
    return []


def detect_unpublished_branch(source_dir: str, branch: str) -> bool:
    """Return True if `branch` has no ref on `origin` in `source_dir`.

    Fail-open: returns False (do not block) when:
    - No 'origin' remote is configured
    - Any git command errors (network issue, non-git dir, etc.)
    - Network probe times out (firewalled remote, unreachable SSH host, etc.)
    Returns True only when origin is reachable and explicitly has no matching ref.
    """
    remote_check = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    if remote_check.returncode != 0:
        return False  # No remote configured — can't confirm, don't block

    try:
        ls_remote = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False  # Network hung — fail-open, don't block the pipeline
    # exit code 2 from --exit-code means "no matching refs found"
    return ls_remote.returncode == 2

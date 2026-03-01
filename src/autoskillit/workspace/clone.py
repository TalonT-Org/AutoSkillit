"""Clone-based run isolation for pipeline recipes.

SOURCE ISOLATION: After clone_repo returns clone_path, the source_dir must
not be touched for any purpose except reading its remote URL in push_to_remote.
This prohibits git checkout, git fetch, git reset, git pull, run_cmd, run_skill,
and every other command in source_dir. All pipeline work runs in clone_path.

L1 module: depends only on stdlib and autoskillit.core.logging.
Three callables are registered as run_python entry points in bundled recipes.

Note: clone_repo uses the default git clone path, which leverages hardlinks when
source and destination are on the same filesystem (fast, low disk usage). This is
safe for single-user ephemeral pipelines. For multi-tenant deployments where repos
may be owned by different users, pass --no-hardlinks to git clone to avoid the
cross-user hardlink risk (CVE-2024-32020).
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)

_RUNS_DIR = "autoskillit-runs"


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


def clone_repo(
    source_dir: str, run_name: str, branch: str = "", strategy: str = ""
) -> dict[str, str]:
    """Clone source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.

    Used as a run_python entry point in pipeline recipes. Raises ValueError if
    source_dir does not exist; raises RuntimeError if git clone fails.

    When source_dir is empty, auto-detects from git rev-parse --show-toplevel.
    Tilde and relative paths are expanded before validation.

    When branch is empty, the current HEAD branch of source_dir is auto-detected
    and used as the clone branch. If the repo is in detached HEAD state, no
    --branch flag is passed (git clones the default branch).

    When strategy is "" (default), checks for uncommitted changes before cloning.
    If changes are found, returns a warning dict instead of cloning. The caller
    may re-invoke with strategy="proceed" (clone remote committed state only) or
    strategy="clone_local" (copytree — includes working-tree changes).

    After this function returns, source_dir is off-limits except for push_to_remote
    reading its remote URL. See module docstring for the full SOURCE ISOLATION contract.

    Returns:
        On success: {"clone_path": str, "source_dir": str}
        On uncommitted changes (strategy=""): {
            "uncommitted_changes": "true",
            "source_dir": str,
            "branch": str,
            "changed_files": str,
            "total_changed": str,
        }
    """
    if not source_dir:
        source_dir = detect_source_dir(str(Path.cwd()))
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(
            f"source_dir does not exist or is not a directory: '{source_dir}' "
            f"(resolved to: '{source}'). "
            f"Provide an absolute path, a path starting with '~', or leave empty "
            f"to auto-detect from git rev-parse --show-toplevel."
        )

    # Branch resolution
    if not branch:
        detected = detect_branch(str(source))
        branch = detected if detected and detected != "HEAD" else ""

    # Uncommitted-changes gate (only when strategy is not explicitly set)
    if strategy == "":
        changed = detect_uncommitted_changes(str(source))
        if changed:
            logger.warning("clone_uncommitted_changes", source=str(source), count=len(changed))
            return {
                "uncommitted_changes": "true",
                "source_dir": str(source),
                "branch": branch,
                "changed_files": "\n".join(changed[:20]),
                "total_changed": str(len(changed)),
            }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    runs_parent = source.parent / _RUNS_DIR
    clone_path = runs_parent / f"{run_name}-{timestamp}"
    runs_parent.mkdir(parents=True, exist_ok=True)

    if strategy == "clone_local":
        shutil.copytree(str(source), str(clone_path))
        logger.info("clone_created_local_copy", clone_path=str(clone_path), source=str(source))
    else:
        cmd = ["git", "clone"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [str(source), str(clone_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "git clone failed:"
                f"\nstderr: {result.stderr.strip()}"
                f"\nstdout: {result.stdout.strip()}"
            )
        logger.info("clone_created", clone_path=str(clone_path), source=str(source), branch=branch)

    return {"clone_path": str(clone_path), "source_dir": str(source)}


def remove_clone(clone_path: str, keep: str = "false") -> dict[str, str]:
    """Remove the clone directory created by clone_repo.

    Set keep='true' to preserve the clone (useful for debugging failed runs).
    Never raises — all errors are caught and returned as metadata.

    Returns:
        {"removed": "true"} on success,
        {"removed": "false", "reason": str} when kept or not found.
    """
    if keep.strip().lower() == "true":
        logger.info("clone_kept", clone_path=clone_path, reason="keep=true")
        return {"removed": "false", "reason": "keep=true"}
    path = Path(clone_path)
    if not path.exists():
        logger.warning("clone_not_found", clone_path=clone_path)
        return {"removed": "false", "reason": "not_found"}
    try:
        shutil.rmtree(path)
        logger.info("clone_removed", clone_path=clone_path)
        return {"removed": "true"}
    except OSError as exc:
        logger.error("clone_remove_failed", clone_path=clone_path, error=str(exc))
        return {"removed": "false", "reason": str(exc)}


def push_to_remote(
    clone_path: str,
    source_dir: str,
    branch: str,
) -> dict[str, str | bool]:
    """Push the merged branch from the clone directly to the upstream remote.

    Reads the upstream remote URL from source_dir using
    'git remote get-url origin' (read-only — no writes to source_dir),
    then runs 'git push <remote_url> <branch>' from clone_path.

    This preserves clone-based pipeline isolation: source_dir is never
    modified. Changes flow: clone_path → upstream remote (e.g. GitHub).

    Returns:
        {"success": True, "stderr": ""} on success,
        {"success": False, "stderr": str} on failure (does not raise).
    """
    url_result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    if url_result.returncode != 0:
        logger.error(
            "push_to_remote_get_url_failed",
            source_dir=source_dir,
            branch=branch,
            stderr=url_result.stderr.strip(),
        )
        return {"success": False, "stderr": url_result.stderr.strip()}

    remote_url = url_result.stdout.strip()
    push_result = subprocess.run(
        ["git", "push", remote_url, branch],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    if push_result.returncode != 0:
        logger.error(
            "push_to_remote_failed",
            clone_path=clone_path,
            remote_url=remote_url,
            branch=branch,
            stderr=push_result.stderr.strip(),
        )
        return {"success": False, "stderr": push_result.stderr.strip()}

    logger.info(
        "push_to_remote_succeeded",
        clone_path=clone_path,
        remote_url=remote_url,
        branch=branch,
    )
    return {"success": True, "stderr": ""}


class DefaultCloneManager:
    """Concrete CloneManager that delegates to module-level clone functions."""

    def clone_repo(
        self, source_dir: str, run_name: str, branch: str = "", strategy: str = ""
    ) -> dict[str, str]:
        return clone_repo(source_dir, run_name, branch=branch, strategy=strategy)

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]:
        return remove_clone(clone_path, keep)

    def push_to_remote(self, clone_path: str, source_dir: str, branch: str) -> dict[str, str]:
        return push_to_remote(clone_path, source_dir, branch)

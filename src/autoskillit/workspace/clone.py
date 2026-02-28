"""Clone-based run isolation for pipeline recipes.

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


def clone_repo(source_dir: str, run_name: str) -> dict[str, str]:
    """Clone source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.

    Used as a run_python entry point in pipeline recipes. Raises ValueError if
    source_dir does not exist; raises RuntimeError if git clone fails.

    When source_dir is empty, auto-detects from git rev-parse --show-toplevel.
    Tilde and relative paths are expanded before validation.

    Returns:
        {"clone_path": str, "source_dir": str}
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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    runs_parent = source.parent / _RUNS_DIR
    clone_path = runs_parent / f"{run_name}-{timestamp}"
    runs_parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", str(source), str(clone_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed:\nstderr: {result.stderr.strip()}\nstdout: {result.stdout.strip()}"
        )
    logger.info("clone_created", clone_path=str(clone_path), source=str(source))
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
) -> dict[str, str]:
    """Push the merged branch from the clone directly to the upstream remote.

    Reads the upstream remote URL from source_dir using
    'git remote get-url origin' (read-only — no writes to source_dir),
    then runs 'git push <remote_url> <branch>' from clone_path.

    This preserves clone-based pipeline isolation: source_dir is never
    modified. Changes flow: clone_path → upstream remote (e.g. GitHub).

    Returns:
        {"success": "true", "stderr": ""} on success,
        {"success": "false", "stderr": str} on failure (does not raise).
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
        return {"success": "false", "stderr": url_result.stderr.strip()}

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
        return {"success": "false", "stderr": push_result.stderr.strip()}

    logger.info(
        "push_to_remote_succeeded",
        clone_path=clone_path,
        remote_url=remote_url,
        branch=branch,
    )
    return {"success": "true", "stderr": ""}


class DefaultCloneManager:
    """Concrete CloneManager that delegates to module-level clone functions."""

    def clone_repo(self, source_dir: str, run_name: str) -> dict[str, str]:
        return clone_repo(source_dir, run_name)

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]:
        return remove_clone(clone_path, keep)

    def push_to_remote(
        self, clone_path: str, source_dir: str, branch: str
    ) -> dict[str, str]:
        return push_to_remote(clone_path, source_dir, branch)

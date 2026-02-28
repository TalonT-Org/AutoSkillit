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


def clone_repo(source_dir: str, run_name: str) -> dict[str, str]:
    """Clone source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.

    Used as a run_python entry point in pipeline recipes. Raises ValueError if
    source_dir does not exist; raises RuntimeError if git clone fails.

    Returns:
        {"clone_path": str, "source_dir": str}
    """
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise ValueError(f"source_dir does not exist: {source_dir}")
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


def push_clone_to_origin(clone_path: str, source_dir: str, branch: str) -> dict[str, str]:
    """Propagate merged branch from the clone back to the source repository.

    Uses git pull --ff-only from the source_dir side. Running pull from the
    source repo avoids Git's receive.denyCurrentBranch restriction, which
    blocks both push and fetch-with-refspec into a checked-out branch of a
    non-bare repository.

    After merge_worktree merges into the clone's main branch, this callable
    propagates the changes to the original source repository.

    Returns:
        {"success": "true", "stderr": ""} on success,
        {"success": "false", "stderr": str} on failure (does not raise).
    """
    result = subprocess.run(
        ["git", "pull", "--ff-only", clone_path, branch],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(
            "push_clone_failed",
            clone_path=clone_path,
            source_dir=source_dir,
            branch=branch,
            stderr=result.stderr.strip(),
        )
        return {"success": "false", "stderr": result.stderr.strip()}
    logger.info(
        "push_clone_succeeded",
        clone_path=clone_path,
        source_dir=source_dir,
        branch=branch,
    )
    return {"success": "true", "stderr": ""}

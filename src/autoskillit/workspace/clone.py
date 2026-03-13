"""Clone-based run isolation for pipeline recipes.

SOURCE ISOLATION: After clone_repo returns clone_path, the source_dir must
not be touched for any purpose except reading its remote URL in push_to_remote.
This prohibits git checkout, git fetch, git reset, git pull, run_cmd, run_skill,
and every other command in source_dir. All pipeline work runs in clone_path.

L1 module: depends only on stdlib and autoskillit.core.logging.
Three callables are registered as run_python entry points in bundled recipes.

Note: The ``clone_local`` strategy (``shutil.copytree``) leverages hardlinks when
source and destination are on the same filesystem (fast, low disk usage). The
default and ``proceed`` strategies clone from the remote URL and do not use hardlinks.
For multi-tenant deployments using ``clone_local``, pass --no-hardlinks to avoid the
cross-user hardlink risk (CVE-2024-32020).
"""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from autoskillit.core import GENERATED_FILES, get_logger, is_protected_branch

logger = get_logger(__name__)

_RUNS_DIR = "autoskillit-runs"

# URL prefixes that unambiguously identify a network remote
_NETWORK_URL_PREFIXES = ("https://", "http://", "git@", "git://", "ssh://", "file://")


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


def _resolve_clone_source(source: Path, detected_url: str) -> str:
    """Select the clone source for the proceed git-clone strategy.

    Always uses the remote URL when source has a configured origin.
    Falls back to the local filesystem path only when no remote origin
    is configured. Never falls back based on branch availability or
    network reachability — when a remote URL is known, it is always
    used as the clone source. The clone_local strategy bypasses this
    function entirely (shutil.copytree, always local).
    """
    return detected_url if detected_url else str(source)


def clone_repo(
    source_dir: str,
    run_name: str,
    branch: str = "",
    strategy: str = "",
    remote_url: str = "",
) -> dict[str, str]:
    """Clone source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.

    Used as a run_python entry point in pipeline recipes. Raises ValueError if
    source_dir does not exist; raises RuntimeError if git clone fails.

    When source_dir is empty, auto-detects from git rev-parse --show-toplevel.
    Tilde and relative paths are expanded before validation.

    When branch is empty, the current HEAD branch of source_dir is auto-detected
    and used as the clone branch. If the repo is in detached HEAD state, no
    --branch flag is passed (git clones the default branch).

    When strategy is "" (default), checks for uncommitted changes and unpublished
    branches before cloning. If either guard fires, returns a warning dict instead
    of cloning. The caller may re-invoke with strategy="proceed" (clone remote
    committed state only) or strategy="clone_local" (copytree — includes working-tree
    changes).

    When using the ``proceed`` strategy, the git clone is always performed from the
    remote URL when source_dir has a configured origin. Falls back to the local path
    only when no remote origin is configured. If a branch is requested that does not
    exist on the remote, git clone will fail with RuntimeError — use
    ``strategy="clone_local"`` to clone a local-only branch. The ``clone_local``
    strategy always copies directly from the local filesystem path regardless of
    remote configuration.

    After this function returns, source_dir is off-limits except for push_to_remote
    reading its remote URL. See module docstring for the full SOURCE ISOLATION contract.

    Returns:
        On success: {"clone_path": str, "source_dir": str, "remote_url": str}
        On uncommitted changes (strategy=""): {
            "uncommitted_changes": "true",
            "source_dir": str,
            "branch": str,
            "changed_files": str,
            "total_changed": str,
        }
        On unpublished branch (strategy=""): {
            "unpublished_branch": "true",
            "branch": str,
            "source_dir": str,
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
        # Unpublished branch guard
        if branch and detect_unpublished_branch(str(source), branch):
            logger.warning("clone_unpublished_branch", source=str(source), branch=branch)
            return {
                "unpublished_branch": "true",
                "branch": branch,
                "source_dir": str(source),
            }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    runs_parent = source.parent / _RUNS_DIR
    clone_path = runs_parent / f"{run_name}-{timestamp}"
    runs_parent.mkdir(parents=True, exist_ok=True)

    try:
        _pre_url_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(source),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        _pre_url_result = None
    detected_url = (
        _pre_url_result.stdout.strip()
        if _pre_url_result is not None and _pre_url_result.returncode == 0
        else ""
    )

    if strategy == "clone_local":
        shutil.copytree(str(source), str(clone_path))
        logger.info("clone_created_local_copy", clone_path=str(clone_path), source=str(source))
    else:
        clone_source = _resolve_clone_source(source, detected_url)
        cmd = ["git", "clone"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [clone_source, str(clone_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "git clone failed:"
                f"\nstderr: {result.stderr.strip()}"
                f"\nstdout: {result.stdout.strip()}"
            )
        logger.info("clone_created", clone_path=str(clone_path), source=str(source), branch=branch)

    # Use caller-supplied override if provided; fall back to pre-clone detected URL
    effective_url = remote_url if remote_url else detected_url

    # Enforce invariant: clone.origin == effective_url at creation time (INIT_ONLY field gate)
    if effective_url:
        rewrite_result = subprocess.run(
            ["git", "remote", "set-url", "origin", effective_url],
            cwd=str(clone_path),
            capture_output=True,
            text=True,
        )
        if rewrite_result.returncode != 0:
            logger.warning(
                "clone_repo_origin_rewrite_failed",
                clone_path=str(clone_path),
                remote_url=effective_url,
                stderr=rewrite_result.stderr.strip(),
            )
            if remote_url:
                return {
                    "error": "remote_url_rewrite_failed",
                    "clone_path": str(clone_path),
                    "remote_url": effective_url,
                    "stderr": rewrite_result.stderr.strip(),
                }

    # Decontaminate: untrack inherited generated files
    ls_gen = subprocess.run(
        ["git", "ls-files", "--", *sorted(GENERATED_FILES)],
        cwd=str(clone_path),
        capture_output=True,
        text=True,
    )
    tracked_gen = [f.strip() for f in ls_gen.stdout.splitlines() if f.strip()]
    if tracked_gen:
        rm_gen = subprocess.run(
            ["git", "rm", "--cached", "--ignore-unmatch", "--", *tracked_gen],
            cwd=str(clone_path),
            capture_output=True,
            text=True,
        )
        if rm_gen.returncode == 0:
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--no-verify",
                    "-m",
                    "chore: untrack generated files inherited from source",
                ],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            )
        else:
            logger.warning(
                "clone_decontaminate_untrack_failed",
                clone_path=str(clone_path),
                stderr=rm_gen.stderr.strip(),
            )

    # Delete on-disk generated files (covers clone_local copytree copies)
    for gen_path in GENERATED_FILES:
        full = clone_path / gen_path
        try:
            os.unlink(full)
        except FileNotFoundError:
            pass

    return {"clone_path": str(clone_path), "source_dir": str(source), "remote_url": effective_url}


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
    source_dir: str = "",
    branch: str = "",
    *,
    remote_url: str = "",
    protected_branches: list[str] | None = None,
) -> dict[str, str | bool]:
    """Push the merged branch from the clone directly to the upstream remote.

    When remote_url is provided (non-empty), it is used directly and source_dir
    is not accessed for URL lookup. This is the preferred calling convention when
    remote_url has been captured from clone_repo at pipeline start.

    When remote_url is empty, falls back to reading the upstream URL from
    source_dir via 'git remote get-url origin' (read-only — no writes to source_dir).

    If the resolved remote URL is a non-bare local repository (branch checked out),
    returns immediately with error_type="local_non_bare_remote" rather than letting
    git push fail with an unhelpful error.

    Returns:
        {"success": True, "stderr": ""} on success,
        {"success": False, "stderr": str} on failure (does not raise).
        {"success": False, "error_type": str, "stderr": str} on classified failure.
    """
    # Protected-branch guard
    if is_protected_branch(branch, protected=protected_branches):
        logger.error(
            "push_to_remote_protected_branch",
            clone_path=clone_path,
            branch=branch,
        )
        return {
            "success": False,
            "error_type": "protected_branch_push",
            "stderr": (
                f"Refusing to push to protected branch '{branch}'. "
                f"Protected branches: {protected_branches or ['main', 'integration', 'stable']}"
            ),
        }

    resolved_url = remote_url
    if not resolved_url:
        if not source_dir:
            logger.error("push_to_remote_no_url_or_source", clone_path=clone_path, branch=branch)
            return {
                "success": False,
                "stderr": "push_to_remote: neither remote_url nor source_dir was provided",
            }
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
        resolved_url = url_result.stdout.strip()

    # Classify the resolved URL to catch non-bare local remotes early
    url_type = classify_remote_url(resolved_url)
    if url_type == "nonbare_local":
        logger.error(
            "push_to_remote_nonbare_local",
            clone_path=clone_path,
            remote_url=resolved_url,
            branch=branch,
        )
        return {
            "success": False,
            "error_type": "local_non_bare_remote",
            "stderr": (
                "push_to_remote: target is a non-bare local repository with the branch "
                "checked out. Use a bare repository or a network remote."
            ),
        }

    push_result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    if push_result.returncode != 0:
        logger.error(
            "push_to_remote_failed",
            clone_path=clone_path,
            remote_url=resolved_url,
            branch=branch,
            stderr=push_result.stderr.strip(),
        )
        return {"success": False, "stderr": push_result.stderr.strip()}

    logger.info(
        "push_to_remote_succeeded",
        clone_path=clone_path,
        remote_url=resolved_url,
        branch=branch,
    )
    return {"success": True, "stderr": ""}


class DefaultCloneManager:
    """Concrete CloneManager that delegates to module-level clone functions."""

    def clone_repo(
        self,
        source_dir: str,
        run_name: str,
        branch: str = "",
        strategy: str = "",
        remote_url: str = "",
    ) -> dict[str, str]:
        return clone_repo(
            source_dir, run_name, branch=branch, strategy=strategy, remote_url=remote_url
        )

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]:
        return remove_clone(clone_path, keep)

    def push_to_remote(
        self,
        clone_path: str,
        source_dir: str = "",
        branch: str = "",
        *,
        remote_url: str = "",
        protected_branches: list[str] | None = None,
    ) -> dict[str, str | bool]:
        return push_to_remote(
            clone_path,
            source_dir,
            branch,
            remote_url=remote_url,
            protected_branches=protected_branches,
        )

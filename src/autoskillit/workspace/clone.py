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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from autoskillit.core import GENERATED_FILES, CloneResult, get_logger, is_protected_branch

logger = get_logger(__name__)

RUNS_DIR = "autoskillit-runs"

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


def _add_or_set_upstream(clone_path: Path, url: str) -> None:
    """Add or update the upstream remote in the clone.

    Handles the case where upstream already exists (clone_local copies .git as-is
    and the source may already have an upstream remote).
    """
    result = subprocess.run(
        ["git", "remote", "add", "upstream", url],
        cwd=str(clone_path),
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.decode(errors="replace")
            if isinstance(result.stderr, bytes)
            else result.stderr
        )
        if "already exists" not in stderr:
            raise RuntimeError(
                f"git remote add upstream failed: {stderr.strip()}"
                f"\nclone_path={clone_path}, url={url}"
            )
        # upstream already exists (e.g. clone_local copied it from source); update the URL
        set_url = subprocess.run(
            ["git", "remote", "set-url", "upstream", url],
            cwd=str(clone_path),
            capture_output=True,
        )
        if set_url.returncode != 0:
            set_stderr = (
                set_url.stderr.decode(errors="replace")
                if isinstance(set_url.stderr, bytes)
                else set_url.stderr
            )
            raise RuntimeError(
                f"git remote set-url upstream failed: {set_stderr.strip()}"
                f"\nclone_path={clone_path}, url={url}"
            )


def _ensure_origin_isolated(clone_path: Path, known_url: str) -> None:
    """Rewrite origin to a self-referential file:// URL unconditionally.

    Claude Code reads 'origin' to resolve the project root; a file:// URL
    cannot match any registered GitHub project, so the clone is treated as
    a fresh project rooted at clone_path — not aliased to the source repo.

    This must fire for every successful clone regardless of URL availability.
    The conditional guard that previously skipped this for clone_local and
    empty-URL cases was the #377 compounding regression.

    After setting origin, calls _add_or_set_upstream with known_url only
    if known_url is truthy — the real remote URL is stored in 'upstream'
    when available.
    """
    file_url = f"file://{clone_path}"
    add_origin = subprocess.run(
        ["git", "remote", "add", "origin", file_url],
        cwd=str(clone_path),
        capture_output=True,
    )
    if add_origin.returncode != 0:
        add_stderr = (
            add_origin.stderr.decode(errors="replace")
            if isinstance(add_origin.stderr, bytes)
            else add_origin.stderr
        )
        if "already exists" not in add_stderr:
            raise RuntimeError(
                f"git remote add origin failed: {add_stderr.strip()}\nclone_path={clone_path}"
            )
        # origin already exists (cloned from remote); rewrite it
        set_origin = subprocess.run(
            ["git", "remote", "set-url", "origin", file_url],
            cwd=str(clone_path),
            capture_output=True,
        )
        if set_origin.returncode != 0:
            set_origin_stderr = (
                set_origin.stderr.decode(errors="replace")
                if isinstance(set_origin.stderr, bytes)
                else set_origin.stderr
            )
            raise RuntimeError(
                f"git remote set-url origin failed: {set_origin_stderr.strip()}"
                f"\nclone_path={clone_path}"
            )
    if known_url:
        _add_or_set_upstream(clone_path, known_url)


@dataclass(frozen=True)
class CloneSourceResolution:
    """Result of probing a source directory for its origin URL.

    reason:
        "ok"        — origin is configured and subprocess returned a URL
        "no_origin" — no origin remote is configured in this repo
        "timeout"   — subprocess.run timed out (30 s)
        "error"     — subprocess returned non-zero exit code
    url: the remote URL (empty string unless reason == "ok")
    stderr: subprocess stderr (empty unless reason == "error")
    """

    reason: Literal["ok", "no_origin", "timeout", "error"]
    url: str
    stderr: str


def _probe_origin_url(source: Path) -> CloneSourceResolution:
    """Probe source for its origin remote URL.

    Runs ``git remote get-url origin`` with a 30-second timeout and
    classifies the result into one of four typed reasons so callers
    cannot collapse the outcomes into a single empty string.
    """
    _no_origin_markers = ("no such remote",)
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(source),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return CloneSourceResolution(reason="timeout", url="", stderr="")
    except OSError as exc:
        return CloneSourceResolution(reason="error", url="", stderr=str(exc))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stderr_lower = stderr.lower()
        if any(marker in stderr_lower for marker in _no_origin_markers):
            return CloneSourceResolution(reason="no_origin", url="", stderr=stderr)
        return CloneSourceResolution(reason="error", url="", stderr=stderr)

    url = result.stdout.strip()
    if not url:
        # git normally returns non-zero for missing origin, but guard defensively
        return CloneSourceResolution(reason="no_origin", url="", stderr="")

    return CloneSourceResolution(reason="ok", url=url, stderr="")


def clone_repo(
    source_dir: str,
    run_name: str,
    branch: str = "",
    strategy: str = "",
    remote_url: str = "",
) -> CloneResult:
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

    When using the ``proceed`` strategy, the git clone is performed from the remote
    URL. If origin is not configured, the subprocess times out, or the probe returns
    a non-zero exit code, RuntimeError is raised — use ``strategy="clone_local"``
    to clone a repo without a remote origin. If a branch is requested that does not
    exist on the remote, git clone will fail with RuntimeError — use
    ``strategy="clone_local"`` to clone a local-only branch. The ``clone_local``
    strategy always copies directly from the local filesystem path regardless of
    remote configuration.

    After this function returns, source_dir is off-limits except for push_to_remote
    reading its remote URL. See module docstring for the full SOURCE ISOLATION contract.

    Returns:
        On success: {
            "clone_path": str,
            "source_dir": str,
            "remote_url": str,
            "clone_source_type": "remote" | "local",
            "clone_source_reason": str,
        }
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
    runs_parent = source.parent / RUNS_DIR
    clone_path = runs_parent / f"{run_name}-{timestamp}"
    runs_parent.mkdir(parents=True, exist_ok=True)

    resolution = _probe_origin_url(source)

    if strategy == "clone_local":
        shutil.copytree(str(source), str(clone_path))
        logger.info("clone_created_local_copy", clone_path=str(clone_path), source=str(source))
        source_type: Literal["remote", "local"] = "local"
        source_reason = "strategy_clone_local"
    else:
        if resolution.reason != "ok":
            logger.warning(
                "clone_origin_probe_failed",
                source=str(source),
                reason=resolution.reason,
                stderr=resolution.stderr,
            )
            raise RuntimeError(
                f"clone_origin_probe_failed: reason={resolution.reason};"
                f" source={source}; stderr={resolution.stderr};"
                f' if a local-only clone is intended, pass strategy="clone_local".'
            )
        cmd = ["git", "clone"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [resolution.url, str(clone_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                "git clone failed:"
                f"\nstderr: {result.stderr.strip()}"
                f"\nstdout: {result.stdout.strip()}"
            )
        logger.info("clone_created", clone_path=str(clone_path), source=str(source), branch=branch)
        source_type = "remote"
        source_reason = "ok"

    # Use caller-supplied override if provided; fall back to probed URL
    effective_url = remote_url if remote_url else resolution.url

    # Unconditionally isolate the clone: set 'origin' to file://<clone_path> for every
    # successful clone regardless of URL availability. This closes the #377 compounding
    # regression where the isolation rewrite was skipped when effective_url was empty.
    _ensure_origin_isolated(clone_path, effective_url)

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

    return {
        "clone_path": str(clone_path),
        "source_dir": str(source),
        "remote_url": effective_url,
        "clone_source_type": source_type,
        "clone_source_reason": source_reason,
    }


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
    force: bool = False,
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
    if protected_branches is None:
        logger.warning(
            "push_to_remote_no_protected_branches",
            clone_path=clone_path,
            branch=branch,
            note="protected_branches not provided; branch protection is disabled for this call",
        )
    if is_protected_branch(branch, protected=protected_branches or []):
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

    push_cmd = ["git", "push", "-u", "upstream", branch]
    if force:
        push_cmd.append("--force-with-lease")
    push_result = subprocess.run(
        push_cmd,
        cwd=clone_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if push_result.returncode != 0:
        stderr_text = push_result.stderr.strip()
        logger.error(
            "push_to_remote_failed",
            clone_path=clone_path,
            remote_url=resolved_url,
            branch=branch,
            stderr=stderr_text,
        )
        failure: dict[str, str | bool] = {"success": False, "stderr": stderr_text}
        if force:
            if "stale info" in stderr_text:
                failure["error_type"] = "force_with_lease_stale"
            elif (
                "no upstream configured" in stderr_text or "has no upstream branch" in stderr_text
            ):
                failure["error_type"] = "force_with_lease_no_upstream"
        return failure

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
    ) -> CloneResult:
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
        force: bool = False,
    ) -> dict[str, str | bool]:
        return push_to_remote(
            clone_path,
            source_dir,
            branch,
            remote_url=remote_url,
            protected_branches=protected_branches,
            force=force,
        )

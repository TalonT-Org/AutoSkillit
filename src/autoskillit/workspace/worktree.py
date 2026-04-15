"""Git worktree lifecycle utilities (L1 workspace service layer).

Provides the single source of truth for worktree creation metadata,
enumeration via `git worktree list --porcelain`, and removal via
`git worktree remove --force` with an shutil.rmtree fallback for
directories that are no longer registered with git (orphans).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from autoskillit.core import CleanupResult, SubprocessRunner, resolve_temp_dir

WORKTREES_DIR = "worktrees"


def _sidecar_root_for(temp_dir: Path) -> Path:
    """Return the sidecar root directory for worktrees (``<temp_dir>/worktrees``)."""
    return temp_dir / WORKTREES_DIR


async def list_git_worktrees(
    project_root: Path,
    worktree_prefix: Path,
    runner: SubprocessRunner,
) -> list[Path]:
    """Return all linked worktrees under *worktree_prefix* registered with git.

    Runs `git -C <project_root> worktree list --porcelain`.
    Returns an empty list on any git error (never raises).
    The main worktree (always first in porcelain output) is excluded.
    """
    result = await runner(
        ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
        cwd=project_root,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    results: list[Path] = []
    first = True
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.split(" ", 1)[1].strip())
            if first:
                first = False
                continue  # skip main worktree
            if str(path).startswith(str(worktree_prefix)):
                results.append(path)
    return results


async def remove_git_worktree(
    worktree_path: Path,
    main_repo: Path,
    runner: SubprocessRunner,
) -> CleanupResult:
    """Remove a git linked worktree, falling back to shutil.rmtree for orphans.

    Strategy:
    1. `git -C <main_repo> worktree remove --force <worktree_path>` (registered worktrees)
    2. `shutil.rmtree` fallback (directories that exist on disk but are not registered)
    3. Record as skipped if path does not exist at all.
    Never raises.
    """
    result = CleanupResult()
    path_str = str(worktree_path)

    if not worktree_path.exists():
        result.skipped.append(path_str)
        return result

    git_result = await runner(
        ["git", "-C", str(main_repo), "worktree", "remove", "--force", path_str],
        cwd=main_repo,
        timeout=30,
    )
    if git_result.returncode == 0:
        result.deleted.append(path_str)
        return result

    # Fallback: orphaned directory not registered with git
    try:
        shutil.rmtree(worktree_path)
        result.deleted.append(path_str)
    except OSError as exc:
        result.failed.append((path_str, str(exc)))
    return result


def remove_worktree_sidecar(
    project_root: Path,
    worktree_name: str,
    *,
    temp_dir: Path | None = None,
) -> CleanupResult:
    """Remove the ``<temp_dir>/worktrees/<worktree_name>/`` sidecar directory.

    This directory is written by implement-worktree and implement-worktree-no-merge
    skills to store the base-branch name. It lives inside the project root
    (under the configured temp dir, default ``.autoskillit/temp``), not inside
    the worktree, so git worktree remove does not clean it up.
    ``temp_dir`` defaults to the canonical ``<project_root>/.autoskillit/temp``.
    Never raises.
    """
    result = CleanupResult()
    resolved_temp = temp_dir if temp_dir is not None else resolve_temp_dir(project_root, None)
    sidecar = _sidecar_root_for(resolved_temp) / worktree_name
    sidecar_str = str(sidecar)
    if not sidecar.exists():
        result.skipped.append(sidecar_str)
        return result
    try:
        shutil.rmtree(sidecar)
        result.deleted.append(sidecar_str)
    except OSError as exc:
        result.failed.append((sidecar_str, str(exc)))
    return result

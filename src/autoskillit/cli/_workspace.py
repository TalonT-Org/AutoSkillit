"""Workspace clean helpers: age partitioning, display, and confirmation."""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from autoskillit.config import load_config
from autoskillit.core import VANISHED_ERRORS, safe_mtime, scan_observed
from autoskillit.execution import DefaultSubprocessRunner
from autoskillit.workspace import (
    RUNS_DIR,
    WORKTREES_DIR,
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
)

_STALE_THRESHOLD_SECONDS = 5 * 3600


def _format_age(seconds: float) -> str:
    """Convert an age in seconds to a human-readable string."""
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    return f"{int(seconds // 86400)}d ago"


def _partition_cleanup_paths(
    paths: Iterable[Path], *, now: float, threshold: float
) -> tuple[list[tuple[Path, float]], list[tuple[Path, float]]]:
    """Partition ordered paths into stale and recent entries, retaining their ages."""
    stale: list[tuple[Path, float]] = []
    recent: list[tuple[Path, float]] = []
    for path in paths:
        mtime = safe_mtime(path)
        if mtime is None:
            continue
        age = now - mtime
        if age >= threshold:
            stale.append((path, age))
        else:
            recent.append((path, age))
    return stale, recent


def _clean_run_directories(
    *,
    runs_dir: Path,
    base: Path,
    force: bool,
    now: float,
    threshold: float,
) -> bool:
    """Return whether a declined run-directory prompt aborts the command."""
    if not runs_dir.is_dir():
        print(f"No {RUNS_DIR}/ directory found under: {base}")
    else:
        paths = (entry for entry in sorted(runs_dir.iterdir()) if entry.is_dir())
        stale, recent = _partition_cleanup_paths(paths, now=now, threshold=threshold)

        if recent:
            print("Skipped (modified < 5h ago):")
            for path, age in recent:
                print(f"  {path.relative_to(runs_dir.parent)}  ({_format_age(age)})")
            print()

        if not stale:
            print(f"Nothing to clean in {runs_dir}")
        else:
            print("Will remove:")
            for path, age in stale:
                print(f"  {path.relative_to(runs_dir.parent)}  ({_format_age(age)})")
            print()

            if not force:
                from autoskillit.cli.ui._timed_input import timed_prompt

                suffix = "ies" if len(stale) != 1 else "y"
                answer = timed_prompt(
                    f"Remove {len(stale)} director{suffix}? [y/N]",
                    default="n",
                    timeout=120,
                    label="autoskillit workspace clean",
                )
                if answer.lower() != "y":
                    print("Aborted.")
                    return True

            count = 0
            errors = 0
            for path, _ in stale:
                try:
                    shutil.rmtree(path)
                    print(f"Removed: {path}")
                    count += 1
                except OSError as exc:
                    print(f"Failed to remove {path}: {exc}", file=sys.stderr)
                    errors += 1

            suffix = "ies" if count != 1 else "y"
            err_note = f" ({errors} error(s))" if errors else ""
            print(f"\nCleaned {count} director{suffix}{err_note}")
    return False


def _confirm_worktree_cleanup(
    *,
    worktrees_dir: Path,
    stale_wts: list[tuple[Path, float]],
    recent_wts: list[tuple[Path, float]],
    force: bool,
) -> bool:
    if recent_wts:
        print("Skipped worktrees (modified < 5h ago):")
        for wt, age in recent_wts:
            print(f"  {wt.name}  ({_format_age(age)})")
        print()

    if not stale_wts:
        print(f"Nothing to clean in {worktrees_dir}")
        return False

    print("Will remove worktrees:")
    for wt, age in stale_wts:
        print(f"  {wt.name}  ({_format_age(age)})")
    print()

    if not force:
        from autoskillit.cli.ui._timed_input import timed_prompt

        suffix = "ies" if len(stale_wts) != 1 else "y"
        answer = timed_prompt(
            f"Remove {len(stale_wts)} worktree director{suffix}? [y/N]",
            default="n",
            timeout=120,
            label="autoskillit workspace clean",
        )
        if answer.lower() != "y":
            print("Aborted.")
            return False
    return True


async def _clean_worktrees(
    *,
    project_root: Path,
    worktrees_dir: Path,
    base: Path,
    force: bool,
    now: float,
    threshold: float,
) -> None:
    if not worktrees_dir.exists():
        print(f"No {WORKTREES_DIR}/ directory found under: {base}")
        return

    runner = DefaultSubprocessRunner()
    git_worktrees = set(await list_git_worktrees(project_root, worktrees_dir, runner))
    try:
        fs_worktrees = {entry.path for entry in scan_observed(worktrees_dir) if entry.is_dir}
    except VANISHED_ERRORS:
        fs_worktrees = set()
    all_worktrees = git_worktrees | fs_worktrees

    # Filter out stale git-registered paths that no longer exist on disk.
    stale_wts, recent_wts = _partition_cleanup_paths(
        sorted(all_worktrees), now=now, threshold=threshold
    )

    if not _confirm_worktree_cleanup(
        worktrees_dir=worktrees_dir,
        stale_wts=stale_wts,
        recent_wts=recent_wts,
        force=force,
    ):
        return

    for wt, _ in stale_wts:
        wt_result = await remove_git_worktree(wt, project_root, runner)
        sidecar_result = remove_worktree_sidecar(project_root, wt.name)
        if not wt_result.success:
            for fail_path, fail_err in wt_result.failed:
                print(f"Failed to remove worktree {fail_path}: {fail_err}", file=sys.stderr)
        if not sidecar_result.success:
            for fail_path, fail_err in sidecar_result.failed:
                print(f"Failed to remove sidecar {fail_path}: {fail_err}", file=sys.stderr)
        if wt_result.success and sidecar_result.success:
            print(f"Removed worktree: {wt.name}")


async def run_workspace_clean(
    *,
    dir: str | None = None,
    force: bool = False,
    project_root: Path | None = None,
) -> None:
    """Core logic for ``workspace clean`` — partitions, displays, confirms, deletes."""
    project_root = project_root or Path.cwd()
    cfg = load_config(project_root)
    base = Path(dir).resolve() if dir else project_root.parent
    now = time.time()
    threshold = _STALE_THRESHOLD_SECONDS

    # --- Clone runs ---
    runs_dir = Path(cfg.workspace.runs_root) if cfg.workspace.runs_root else base / RUNS_DIR
    prompt_aborted = _clean_run_directories(
        runs_dir=runs_dir, base=base, force=force, now=now, threshold=threshold
    )
    if prompt_aborted:
        return

    # --- Git worktrees ---
    worktrees_dir = (
        Path(cfg.workspace.worktree_root) if cfg.workspace.worktree_root else base / WORKTREES_DIR
    )
    await _clean_worktrees(
        project_root=project_root,
        worktrees_dir=worktrees_dir,
        base=base,
        force=force,
        now=now,
        threshold=threshold,
    )

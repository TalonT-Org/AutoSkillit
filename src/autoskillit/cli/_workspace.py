"""Workspace clean helpers: age partitioning, display, and confirmation."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from autoskillit.config import load_config
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
    if not runs_dir.is_dir():
        print(f"No {RUNS_DIR}/ directory found under: {base}")
    else:
        stale: list[tuple[Path, float]] = []
        recent: list[tuple[Path, float]] = []
        for entry in sorted(runs_dir.iterdir()):
            if entry.is_dir():
                age = now - entry.stat().st_mtime
                if age >= threshold:
                    stale.append((entry, age))
                else:
                    recent.append((entry, age))

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
                    return

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

    # --- Git worktrees ---
    worktrees_dir = (
        Path(cfg.workspace.worktree_root) if cfg.workspace.worktree_root else base / WORKTREES_DIR
    )
    if not worktrees_dir.exists():
        print(f"No {WORKTREES_DIR}/ directory found under: {base}")
        return

    runner = DefaultSubprocessRunner()
    git_worktrees = set(await list_git_worktrees(project_root, worktrees_dir, runner))
    try:
        fs_worktrees = {p for p in worktrees_dir.iterdir() if p.is_dir()}
    except FileNotFoundError:
        fs_worktrees = set()
    all_worktrees = git_worktrees | fs_worktrees

    # Filter out stale git-registered paths that no longer exist on disk.
    def _safe_mtime(p: Path) -> float | None:
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return None

    stale_wts: list[Path] = []
    recent_wts: list[Path] = []
    for p in sorted(all_worktrees):
        mtime = _safe_mtime(p)
        if mtime is None:
            continue
        if now - mtime >= threshold:
            stale_wts.append(p)
        else:
            recent_wts.append(p)

    if recent_wts:
        print("Skipped worktrees (modified < 5h ago):")
        for wt in recent_wts:
            print(f"  {wt.name}  ({_format_age(now - wt.stat().st_mtime)})")
        print()

    if not stale_wts:
        print(f"Nothing to clean in {worktrees_dir}")
        return

    print("Will remove worktrees:")
    for wt in stale_wts:
        print(f"  {wt.name}  ({_format_age(now - wt.stat().st_mtime)})")
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
            return

    for wt in stale_wts:
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

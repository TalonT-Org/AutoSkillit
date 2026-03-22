"""Workspace clean helpers: age partitioning, display, and confirmation."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


def _format_age(seconds: float) -> str:
    """Convert an age in seconds to a human-readable string."""
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    return f"{int(seconds // 86400)}d ago"


def run_workspace_clean(*, dir: str | None = None, force: bool = False) -> None:
    """Core logic for ``workspace clean`` — partitions, displays, confirms, deletes."""
    base = Path(dir).resolve() if dir else Path.cwd().parent
    runs_dir = base / "autoskillit-runs"

    if not runs_dir.is_dir():
        print(f"No autoskillit-runs/ directory found under: {base}")
        return

    now = time.time()
    threshold = 5 * 3600  # 5 hours in seconds
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
        return

    print("Will remove:")
    for path, age in stale:
        print(f"  {path.relative_to(runs_dir.parent)}  ({_format_age(age)})")
    print()

    if not force:
        from autoskillit.cli._init_helpers import _require_interactive_stdin

        _require_interactive_stdin("autoskillit workspace clean")
        suffix = "ies" if len(stale) != 1 else "y"
        answer = input(f"Remove {len(stale)} director{suffix}? [y/N]: ")
        if answer.strip().lower() != "y":
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

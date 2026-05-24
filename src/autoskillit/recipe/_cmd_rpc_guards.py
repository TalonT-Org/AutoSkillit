"""Recipe cmd externalization guards — counter guards and git workspace ops."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, is_generated_path

logger = get_logger(__name__)


def compute_branch(
    issue_slug: str = "",
    run_name: str = "",
    issue_number: str = "",
) -> dict[str, str]:
    """Compute branch name from slug + issue or date."""
    prefix = issue_slug or run_name
    if issue_number:
        return {"branch_name": f"{prefix}/{issue_number}"}
    return {"branch_name": f"{prefix}/{date.today().strftime('%Y%m%d')}"}


def check_eject_limit(
    counter_file: str,
    max_ejects: str = "3",
) -> dict[str, str]:
    """Increment counter file; return EJECT_OK or EJECT_LIMIT_EXCEEDED."""
    max_ejects = max_ejects or "3"
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "EJECT_LIMIT_EXCEEDED" if count > int(max_ejects) else "EJECT_OK"
    return {"status": status, "count": str(count)}


def check_dropped_healthy_loop(
    counter_file: str,
    max_drops: str = "2",
) -> dict[str, str]:
    """Increment dropped-healthy counter; return DROPPED_OK or DROPPED_LIMIT_EXCEEDED."""
    max_drops = max_drops or "2"
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "DROPPED_LIMIT_EXCEEDED" if count > int(max_drops) else "DROPPED_OK"
    return {"status": status, "count": str(count)}


def main_repo_guard(clone_path: str) -> dict[str, str]:
    """Stash dirty state from the main repo before merge."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )

    if not result.stdout.strip():
        return {"cleaned": "false"}

    # Detect and remove linked worktrees nested inside the clone.
    clone_resolved = Path(clone_path).resolve()
    wt_list = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=clone_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if wt_list.returncode == 0:
        first = True
        for line in wt_list.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            if first:
                first = False
                continue  # main worktree is always first in porcelain output
            wt_path = Path(line.split(" ", 1)[1].strip())
            if wt_path.resolve().is_relative_to(clone_resolved):
                rm_result = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if rm_result.returncode != 0 and wt_path.exists():
                    shutil.rmtree(wt_path, ignore_errors=True)

    stash_result = subprocess.run(
        [
            "git",
            "stash",
            "--include-untracked",
            "-m",
            "autoskillit: main_repo_guard pre-merge stash",
        ],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    if stash_result.returncode != 0:
        logger.warning(
            "git stash failed (rc=%d) — falling back to force-clean: %s",
            stash_result.returncode,
            stash_result.stderr.strip(),
        )
        co = subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if co.returncode != 0:
            logger.warning(
                "git checkout force-clean failed (rc=%d): %s",
                co.returncode,
                co.stderr.strip(),
            )
        cl = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if cl.returncode != 0:
            logger.warning(
                "git clean force-clean failed (rc=%d): %s",
                cl.returncode,
                cl.stderr.strip(),
            )
        if co.returncode != 0 and cl.returncode != 0:
            return {"cleaned": "failed"}
        verify = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if verify.returncode == 0 and verify.stdout.strip():
            remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[
                :200
            ]
            return {"cleaned": "failed", "remaining": remaining}
        return {"cleaned": "force"}

    verify = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=clone_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if verify.returncode == 0 and verify.stdout.strip():
        remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[:200]
        return {"cleaned": "failed", "remaining": remaining}
    return {"cleaned": "true"}


def commit_guard(worktree_path: str) -> dict[str, str]:
    """Auto-commit pending changes if worktree is dirty, excluding generated files."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"],
        cwd=worktree_path,
        capture_output=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    files_to_add: list[str] = []
    parts = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) < 3:
            i += 1
            continue
        xy = entry[:2]
        path = entry[3:]
        if xy[0] in "RC":
            i += 1
        if path and not is_generated_path(path):
            files_to_add.append(path)
        i += 1

    if not files_to_add:
        return {"committed": "false"}

    subprocess.run(["git", "add", "--", *files_to_add], cwd=worktree_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: commit pending session changes"],
        cwd=worktree_path,
        check=True,
    )
    return {"committed": "true"}

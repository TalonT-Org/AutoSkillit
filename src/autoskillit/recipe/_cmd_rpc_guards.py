"""Recipe cmd externalization guards — counter guards and git workspace ops."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, is_generated_path, run_git

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
    """Discard dirty state from the main repo before merge (ephemeral clone)."""
    result = run_git(["status", "--porcelain"], cwd=clone_path)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )

    if not result.stdout.strip():
        return {"cleaned": "false"}

    # Detect and remove linked worktrees nested inside the clone.
    clone_resolved = Path(clone_path).resolve()
    wt_list = run_git(["worktree", "list", "--porcelain"], cwd=clone_path)
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
                rm_result = run_git(
                    ["worktree", "remove", "--force", str(wt_path)], cwd=clone_path
                )
                if rm_result.returncode != 0 and wt_path.exists():
                    shutil.rmtree(wt_path, ignore_errors=True)

    stash_result = run_git(
        ["stash", "--include-untracked", "-m", "autoskillit: main_repo_guard pre-merge stash"],
        cwd=clone_path,
    )
    if stash_result.returncode != 0:
        logger.warning(
            "git stash failed (rc=%d) — falling back to force-clean: %s",
            stash_result.returncode,
            stash_result.stderr.strip(),
        )
        co = run_git(["checkout", "--", "."], cwd=clone_path)
        if co.returncode != 0:
            logger.warning(
                "git checkout force-clean failed (rc=%d): %s",
                co.returncode,
                co.stderr.strip(),
            )
        cl = run_git(["clean", "-fd"], cwd=clone_path)
        if cl.returncode != 0:
            logger.warning(
                "git clean force-clean failed (rc=%d): %s",
                cl.returncode,
                cl.stderr.strip(),
            )
        if co.returncode != 0 and cl.returncode != 0:
            return {"cleaned": "failed"}
        verify = run_git(["status", "--porcelain"], cwd=clone_path)
        if verify.returncode == 0 and verify.stdout.strip():
            remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[
                :200
            ]
            return {"cleaned": "failed", "remaining": remaining}
        return {"cleaned": "force"}

    verify = run_git(["status", "--porcelain"], cwd=clone_path)
    if verify.returncode == 0 and verify.stdout.strip():
        remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[:200]
        return {"cleaned": "failed", "remaining": remaining}
    return {"cleaned": "true"}


def _count_numstat_net(output: str) -> int:
    """Sum net insertions (insertions - deletions) from git diff --numstat output."""
    total = 0
    for line in output.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            try:
                total += int(parts[0]) - int(parts[1])
            except ValueError:
                pass
    return total


def _check_regression(
    worktree_path: str, files_to_add: list[str], base_branch: str
) -> dict[str, str] | None:
    """Detect if uncommitted changes regress the implementation versus base_branch.

    Compares working-tree delta vs committed-only delta (both against merge-base).
    Returns a regression_detected dict if the uncommitted changes would reduce the
    implementation's net contribution by more than 10 lines; else returns None.
    """
    mb = subprocess.run(
        ["git", "merge-base", "HEAD", base_branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if mb.returncode != 0 or not mb.stdout.strip():
        return None  # No common ancestor (fresh repo) — skip check
    merge_base_sha = mb.stdout.strip()

    committed = subprocess.run(
        ["git", "diff", "--numstat", merge_base_sha, "HEAD", "--", *files_to_add],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    committed_net = _count_numstat_net(committed.stdout)
    if committed_net <= 0:
        return None  # No implementation commits yet — skip check

    wt = subprocess.run(
        ["git", "diff", "--numstat", merge_base_sha, "--", *files_to_add],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if wt.returncode != 0:
        return None  # git diff failed (e.g., invalid merge-base) — skip regression check
    wt_net = _count_numstat_net(wt.stdout)

    if committed_net - wt_net <= 10:
        return None  # Uncommitted changes do not meaningfully reduce the implementation

    # Identify per-file regressions (per-file delta loss > 5 lines)
    reverted: list[str] = []
    for f in files_to_add:
        f_c = subprocess.run(
            ["git", "diff", "--numstat", merge_base_sha, "HEAD", "--", f],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        f_w = subprocess.run(
            ["git", "diff", "--numstat", merge_base_sha, "--", f],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if _count_numstat_net(f_c.stdout) - _count_numstat_net(f_w.stdout) > 5:
            reverted.append(f)

    # Collect insertion/deletion totals from working-tree diff for diagnostics
    total_ins = 0
    total_del = 0
    for line in wt.stdout.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            try:
                total_ins += int(parts[0])
                total_del += int(parts[1])
            except ValueError:
                pass

    return {
        "committed": "regression_detected",
        "reverted_files": ", ".join(reverted),
        "insertions": str(total_ins),
        "deletions": str(total_del),
    }


def commit_guard(worktree_path: str, base_branch: str = "") -> dict[str, str]:
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

    if base_branch:
        regression = _check_regression(worktree_path, files_to_add, base_branch)
        if regression is not None:
            return regression

    run_git(["add", "--", *files_to_add], cwd=worktree_path, check=True)
    run_git(
        ["commit", "-m", "chore: commit pending session changes"], cwd=worktree_path, check=True
    )
    return {"committed": "true"}

"""Git merge workflow for the merge_worktree MCP tool.

L3 service module. Executes the full merge pipeline:
path validation → worktree verification → branch detection → test gate →
fetch → rebase → main-repo merge → worktree cleanup.

Public API:
    perform_merge(worktree_path, base_branch, *, config, runner) -> dict
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    MergeFailedStep,
    MergeState,
    SubprocessRunner,
    get_logger,
    truncate_text,
)
from autoskillit.server.helpers import _process_runner_result

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import TestRunner

logger = get_logger(__name__)


def _filter_changed_files(stdout: str, prefixes: list[str]) -> tuple[list[str], list[str]]:
    """Parse git diff stdout into (changed_files, critical_files).

    critical_files are those matching any of the configured path prefixes.
    """
    changed_files = [f.strip() for f in stdout.splitlines() if f.strip()]
    critical_files = [f for f in changed_files if any(f.startswith(p) for p in prefixes)]
    return changed_files, critical_files


async def _run_git(
    cmd: list[str],
    cwd: str | Path,
    timeout: float,
    runner: SubprocessRunner,
) -> tuple[int, str, str]:
    """Run a single git command via the injected subprocess runner.

    Returns (returncode, stdout, stderr). Handles TIMED_OUT termination.
    """
    result = await runner(cmd, cwd=Path(cwd), timeout=timeout)
    return _process_runner_result(result, timeout)


async def perform_merge(
    worktree_path: str,
    base_branch: str,
    *,
    config: AutomationConfig,
    runner: SubprocessRunner,
    tester: TestRunner | None = None,
) -> dict[str, Any]:
    """Execute the full merge pipeline for a git worktree.

    Returns a dict that server/__init__.py serializes to JSON. On failure, the dict
    contains an 'error' key along with 'failed_step', 'state', and
    'worktree_path' for downstream diagnosis.
    """
    # 1. Path existence check
    if not os.path.isdir(worktree_path):
        return {"error": f"Path does not exist: {worktree_path}"}

    # 2. Verify it is a git worktree (not a plain repo)
    rc, git_dir, stderr = await _run_git(
        ["git", "rev-parse", "--git-dir"], worktree_path, 10, runner
    )
    if rc != 0 or "/worktrees/" not in git_dir:
        return {"error": f"Not a git worktree: {worktree_path}", "stderr": stderr}

    # 3. Get branch name
    rc, branch_out, stderr = await _run_git(
        ["git", "branch", "--show-current"], worktree_path, 10, runner
    )
    if rc != 0:
        return {"error": f"Could not determine branch: {stderr}"}
    worktree_branch = branch_out.strip()

    # 4. Test gate
    if config.safety.test_gate_on_merge:
        if tester is None:
            return {
                "error": "Test gate required but no tester configured",
                "failed_step": MergeFailedStep.TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
            }
        passed, _ = await tester.run(Path(worktree_path))
        if not passed:
            return {
                "error": "Tests failed in worktree — merge blocked",
                "failed_step": MergeFailedStep.TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
            }

    # 5. Fetch
    fetch_rc, _, fetch_stderr = await _run_git(
        ["git", "fetch", "origin"], worktree_path, 60, runner
    )
    if fetch_rc != 0:
        return {
            "error": "git fetch origin failed",
            "failed_step": MergeFailedStep.FETCH,
            "state": MergeState.WORKTREE_INTACT,
            "stderr": truncate_text(fetch_stderr),
            "worktree_path": worktree_path,
        }

    # 6. Rebase
    rc, _, rebase_stderr = await _run_git(
        ["git", "rebase", f"origin/{base_branch}"], worktree_path, 120, runner
    )
    if rc != 0:
        await _run_git(["git", "rebase", "--abort"], worktree_path, 30, runner)
        return {
            "error": "Rebase failed — aborted to clean state",
            "failed_step": MergeFailedStep.REBASE,
            "state": MergeState.WORKTREE_INTACT_REBASE_ABORTED,
            "stderr": rebase_stderr,
            "worktree_path": worktree_path,
        }

    # 7. Discover main repo path
    rc, wt_list, _ = await _run_git(
        ["git", "worktree", "list", "--porcelain"], worktree_path, 10, runner
    )
    main_repo = ""
    for line in wt_list.splitlines():
        if line.startswith("worktree "):
            main_repo = line.split(" ", 1)[1].strip()
            break  # First entry is always the main working tree
    if not main_repo:
        return {"error": "Could not determine main repo path from worktree list"}

    # 8. Merge
    rc, _, merge_stderr = await _run_git(["git", "merge", worktree_branch], main_repo, 60, runner)
    if rc != 0:
        await _run_git(["git", "merge", "--abort"], main_repo, 30, runner)
        return {
            "error": "Merge failed — aborted to clean state",
            "failed_step": MergeFailedStep.MERGE,
            "state": MergeState.MAIN_REPO_MERGE_ABORTED,
            "stderr": merge_stderr,
            "worktree_path": worktree_path,
        }

    # 9. Cleanup
    wt_rc, _, wt_stderr = await _run_git(
        ["git", "worktree", "remove", worktree_path], main_repo, 30, runner
    )
    if wt_rc != 0:
        logger.warning(
            "merge_worktree_cleanup_failed",
            operation="worktree_remove",
            path=worktree_path,
            stderr=wt_stderr.strip(),
        )

    br_rc, _, br_stderr = await _run_git(
        ["git", "branch", "-D", worktree_branch], main_repo, 10, runner
    )
    if br_rc != 0:
        logger.warning(
            "merge_worktree_cleanup_failed",
            operation="branch_delete",
            branch=worktree_branch,
            stderr=br_stderr.strip(),
        )

    return {
        "merge_succeeded": True,
        "merged_branch": worktree_branch,
        "into_branch": base_branch,
        "worktree_removed": wt_rc == 0,
        "branch_deleted": br_rc == 0,
        "cleanup_succeeded": wt_rc == 0 and br_rc == 0,
    }

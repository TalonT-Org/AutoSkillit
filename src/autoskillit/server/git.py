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
    TerminationReason,
    get_logger,
    truncate_text,
)

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
    if result.termination == TerminationReason.TIMED_OUT:
        return -1, result.stdout, f"Process timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


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
        return {
            "error": f"Path does not exist: {worktree_path}",
            "failed_step": MergeFailedStep.PATH_VALIDATION,
            "state": MergeState.WORKTREE_INTACT,
            "worktree_path": worktree_path,
        }

    # 2. Verify it is a git worktree (not a plain repo)
    rc, git_dir, stderr = await _run_git(
        ["git", "rev-parse", "--git-dir"], worktree_path, 10, runner
    )
    if rc != 0 or "/worktrees/" not in git_dir:
        return {
            "error": f"Not a git worktree: {worktree_path}",
            "failed_step": MergeFailedStep.PATH_VALIDATION,
            "state": MergeState.WORKTREE_INTACT,
            "stderr": stderr,
            "worktree_path": worktree_path,
        }

    # 3. Get branch name
    rc, branch_out, stderr = await _run_git(
        ["git", "branch", "--show-current"], worktree_path, 10, runner
    )
    if rc != 0:
        return {
            "error": f"Could not determine branch: {stderr}",
            "failed_step": MergeFailedStep.BRANCH_DETECTION,
            "state": MergeState.WORKTREE_INTACT,
            "worktree_path": worktree_path,
        }
    worktree_branch = branch_out.strip()
    if not worktree_branch:
        return {
            "error": (
                "Worktree is in detached HEAD state — "
                "possibly mid-rebase from a prior failed attempt. "
                "Run 'git rebase --abort' in the worktree before retrying."
            ),
            "failed_step": MergeFailedStep.BRANCH_DETECTION,
            "state": MergeState.WORKTREE_INTACT,
            "worktree_path": worktree_path,
        }

    # Pre-condition: check for active rebase using git directory state files.
    # Uses directory presence (not REBASE_HEAD file) to avoid false positives
    # from stale files left by third-party git tools after a completed rebase.
    git_dir_path = Path(git_dir.strip())
    if (git_dir_path / "rebase-merge").is_dir() or (git_dir_path / "rebase-apply").is_dir():
        return {
            "error": (
                "Worktree has a rebase in progress from a prior attempt. "
                "Run 'git rebase --abort' in the worktree and retry."
            ),
            "failed_step": MergeFailedStep.REBASE,
            "state": MergeState.WORKTREE_DIRTY_MID_OPERATION,
            "worktree_path": worktree_path,
        }

    # 4. Test gate
    if config.safety.test_gate_on_merge:
        if tester is None:
            return {
                "error": "Test gate required but no tester configured",
                "failed_step": MergeFailedStep.TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
            }
        passed, test_stdout = await tester.run(Path(worktree_path))
        if not passed:
            return {
                "error": "Tests failed in worktree — merge blocked",
                "failed_step": MergeFailedStep.TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
                "test_output": test_stdout,
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

    # 5.5. Verify base branch remote tracking ref exists
    ref_rc, _, _ = await _run_git(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{base_branch}"],
        worktree_path,
        10,
        runner,
    )
    if ref_rc != 0:
        return {
            "error": (
                f"Base branch '{base_branch}' has no remote tracking ref — "
                f"push it to origin before running this pipeline: "
                f"git push -u origin {base_branch}"
            ),
            "failed_step": MergeFailedStep.PRE_REBASE_CHECK,
            "state": MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED,
            "base_branch": base_branch,
            "worktree_path": worktree_path,
        }

    # 6. Rebase
    rc, _, rebase_stderr = await _run_git(
        ["git", "rebase", f"origin/{base_branch}"], worktree_path, 120, runner
    )
    if rc != 0:
        abort_rc, _, abort_stderr = await _run_git(
            ["git", "rebase", "--abort"], worktree_path, 30, runner
        )
        abort_failed = abort_rc != 0
        return {
            "error": (
                "Rebase failed — worktree may still be dirty (abort also failed)"
                if abort_failed
                else "Rebase failed — aborted to clean state"
            ),
            "failed_step": MergeFailedStep.REBASE,
            "state": (
                MergeState.WORKTREE_DIRTY_ABORT_FAILED
                if abort_failed
                else MergeState.WORKTREE_INTACT_REBASE_ABORTED
            ),
            "stderr": rebase_stderr,
            "worktree_path": worktree_path,
            **({"abort_failed": True, "abort_stderr": abort_stderr} if abort_failed else {}),
        }

    # 6.5. Post-rebase test gate — re-tests the rebased commits before merging
    if tester is not None and config.safety.test_gate_on_merge:
        passed, _ = await tester.run(Path(worktree_path))
        if not passed:
            return {
                "error": (
                    "Tests failed after rebase. The worktree is intact; "
                    "run resolve-failures to fix the regressions."
                ),
                "failed_step": MergeFailedStep.POST_REBASE_TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
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
        return {
            "error": "Could not locate main repository from worktree list",
            "failed_step": MergeFailedStep.MERGE,
            "state": MergeState.WORKTREE_INTACT,
            "worktree_path": worktree_path,
        }

    # 8. Merge
    rc, _, merge_stderr = await _run_git(
        ["git", "merge", "--no-edit", worktree_branch], main_repo, 60, runner
    )
    if rc != 0:
        abort_rc, _, abort_stderr = await _run_git(
            ["git", "merge", "--abort"], main_repo, 30, runner
        )
        abort_failed = abort_rc != 0
        return {
            "error": (
                "Merge failed — main repo may still be dirty (abort also failed)"
                if abort_failed
                else "Merge failed — aborted to clean state"
            ),
            "failed_step": MergeFailedStep.MERGE,
            "state": (
                MergeState.MAIN_REPO_DIRTY_ABORT_FAILED
                if abort_failed
                else MergeState.MAIN_REPO_MERGE_ABORTED
            ),
            "stderr": merge_stderr,
            "worktree_path": worktree_path,
            **({"abort_failed": True, "abort_stderr": abort_stderr} if abort_failed else {}),
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

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
    GENERATED_FILES,
    MergeFailedStep,
    MergeState,
    SubprocessRunner,
    TerminationReason,
    get_logger,
    is_protected_branch,
    truncate_text,
)

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import TestRunner

logger = get_logger(__name__)


def _is_generated_path(file_path: str) -> bool:
    """Return True if file_path matches any GENERATED_FILES entry.

    Handles both exact-path entries (e.g. 'src/autoskillit/hooks/hooks.json')
    and directory-prefix entries ending with '/' (e.g. 'src/autoskillit/recipes/diagrams/').
    """
    for entry in GENERATED_FILES:
        if entry.endswith("/"):
            if file_path.startswith(entry):
                return True
        elif file_path == entry:
            return True
    return False


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

    # 1.5. Protected-branch guard
    protected = config.safety.protected_branches
    if is_protected_branch(base_branch, protected=protected):
        return {
            "error": (
                f"Refusing to merge into protected branch '{base_branch}'. "
                f"Protected branches: {protected}"
            ),
            "failed_step": MergeFailedStep.PROTECTED_BRANCH,
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

    # 3c. Strip tracked generated files before dirty-tree check and rebase.
    # Moving this before the rebase prevents conflicts for in-flight branches
    # that already committed generated files (e.g. diagram files).
    ls_rc, ls_out, _ = await _run_git(
        ["git", "ls-files", "--", *sorted(GENERATED_FILES)], worktree_path, 10, runner
    )
    tracked_generated = [f.strip() for f in ls_out.splitlines() if f.strip()]
    if tracked_generated:
        rm_rc, _, rm_stderr = await _run_git(
            ["git", "rm", "--cached", "--ignore-unmatch", "--", *tracked_generated],
            worktree_path,
            10,
            runner,
        )
        if rm_rc != 0:
            return {
                "error": f"Failed to untrack generated files: {rm_stderr}",
                "failed_step": MergeFailedStep.GENERATED_FILE_CLEANUP,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
            }
        commit_rc, _, commit_stderr = await _run_git(
            ["git", "commit", "--no-verify", "-m", "chore: untrack generated files"],
            worktree_path,
            10,
            runner,
        )
        if commit_rc != 0:
            return {
                "error": f"Failed to commit generated file cleanup: {commit_stderr}",
                "failed_step": MergeFailedStep.GENERATED_FILE_CLEANUP,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
            }

    # 3d. Dirty-tree check — reject worktrees with uncommitted non-generated changes.
    # Generated file paths (matching GENERATED_FILES) are filtered out before
    # failing: they may appear as untracked after the strip above, and after
    # FRICT-3B-1 they will be gitignored, but filtering here is a belt-and-suspenders
    # guard for worktrees on older commits that predate the .gitignore update.
    dirty_rc, dirty_out, _ = await _run_git(
        ["git", "status", "--porcelain"], worktree_path, 10, runner
    )
    if dirty_rc == 0 and dirty_out.strip():
        all_dirty = [line.strip() for line in dirty_out.strip().splitlines()]
        # Porcelain format: "XY path" — strip the 2-char status code + space
        dirty_files = [line for line in all_dirty if not _is_generated_path(line[3:])]
        if dirty_files:
            return {
                "error": (
                    f"Worktree has {len(dirty_files)} dirty file(s). "
                    "All changes must be committed before merge."
                ),
                "dirty_files": dirty_files,
                "failed_step": MergeFailedStep.DIRTY_TREE,
                "state": MergeState.WORKTREE_DIRTY,
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
        test_result = await tester.run(Path(worktree_path))
        if not test_result.passed:
            return {
                "error": "Tests failed in worktree — merge blocked",
                "failed_step": MergeFailedStep.TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
                "test_stdout": test_result.stdout,
                "test_stderr": test_result.stderr,
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

    # 5.6. Pre-flight: detect merge commits in worktree history not yet on base branch.
    # Standard git rebase cannot replay merge commits and fails with a generic error.
    # Catch this early with a specific, actionable message.
    mc_rc, mc_out, _ = await _run_git(
        ["git", "log", "--merges", "--oneline", f"origin/{base_branch}..HEAD"],
        worktree_path,
        15,
        runner,
    )
    if mc_rc == 0 and mc_out.strip():
        merge_list = [line.strip() for line in mc_out.strip().splitlines() if line.strip()]
        return {
            "error": (
                f"Worktree branch contains {len(merge_list)} merge commit(s) not yet in "
                f"origin/{base_branch}. Standard git rebase cannot replay merge commits. "
                "The conflict-resolution plan must use 'git cherry-pick' or "
                "'git checkout <remote> -- <file>' to produce a linear commit history. "
                "Route this failure to cleanup_failure — do NOT use run_cmd to bypass."
            ),
            "failed_step": MergeFailedStep.MERGE_COMMITS_DETECTED,
            "state": MergeState.WORKTREE_INTACT_MERGE_COMMITS_DETECTED,
            "merge_commits": merge_list,
            "worktree_path": worktree_path,
        }

    # 6. Rebase
    rc, _, rebase_stderr = await _run_git(
        ["git", "rebase", "--autostash", f"origin/{base_branch}"], worktree_path, 120, runner
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
        test_result = await tester.run(Path(worktree_path))
        passed = test_result.passed
        if not passed:
            return {
                "error": (
                    "Tests failed after rebase. The worktree is intact; "
                    "run resolve-failures to fix the regressions."
                ),
                "failed_step": MergeFailedStep.POST_REBASE_TEST_GATE,
                "state": MergeState.WORKTREE_INTACT,
                "worktree_path": worktree_path,
                "test_stdout": test_result.stdout,
                "test_stderr": test_result.stderr,
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
        ["git", "worktree", "remove", "--force", worktree_path], main_repo, 30, runner
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

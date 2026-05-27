"""Clone contamination guard — detect and revert direct changes to clone CWD.

IL-1 module (execution/). Detects when a worktree-based or read-only skill
session modified the clone directory directly and reverts those changes to
prevent contamination from propagating to retry sessions.

Public API:
    is_worktree_skill(skill_command) -> bool
    snapshot_clone_state(cwd, runner) -> CloneSnapshot | None
    check_and_revert_clone_contamination(
        snapshot, skill_result, cwd, runner, audit
    ) -> tuple[SkillResult, bool]
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    FailureRecord,
    RetryReason,
    SkillResult,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import AuditLog, SubprocessRunner

logger = get_logger(__name__)

WORKTREE_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
        "implement-experiment",
        "retry-worktree",
    }
)

CLONE_COMMIT_SKILLS: frozenset[str] = frozenset(
    {
        "resolve-failures",
        "resolve-review",
        "resolve-merge-conflicts",
    }
)

_GIT_TIMEOUT: float = 10.0


@dataclass(frozen=True, slots=True)
class CloneSnapshot:
    """Pre-session state of the clone directory."""

    head_sha: str


@dataclass(frozen=True, slots=True)
class ContaminationReport:
    """Details of detected clone contamination."""

    pre_sha: str
    post_sha: str
    uncommitted_files: list[str]
    direct_commits: bool
    reverted: bool


@dataclass(frozen=True, slots=True)
class CloneGuardPolicy:
    """Structured session permission policy for the clone guard."""

    _fire_on_success: bool
    selective_revert: bool
    should_snapshot: bool

    def should_fire(self, success: bool) -> bool:
        if not success:
            return True
        return self._fire_on_success


def build_clone_guard_policy(
    *,
    readonly_skill: bool,
    has_write_scope: bool,
    is_clone_commit: bool,
    is_worktree: bool,
    writes_under_exclude: bool = False,
) -> CloneGuardPolicy:
    """Build a CloneGuardPolicy from session properties."""
    fire_on_success = (
        not is_clone_commit
        and not is_worktree
        and not writes_under_exclude
        and (readonly_skill or has_write_scope)
    )
    selective_revert = readonly_skill or has_write_scope
    should_snapshot = is_worktree or readonly_skill or has_write_scope
    return CloneGuardPolicy(
        _fire_on_success=fire_on_success,
        selective_revert=selective_revert,
        should_snapshot=should_snapshot,
    )


def is_worktree_skill(skill_command: str) -> bool:
    """Return True if skill_command invokes a worktree-creating skill."""
    return any(name in skill_command for name in WORKTREE_SKILLS)


def is_clone_commit_skill(skill_command: str) -> bool:
    """Return True if skill_command invokes a skill that legitimately commits to clones."""
    return any(name in skill_command for name in CLONE_COMMIT_SKILLS)


async def snapshot_clone_state(cwd: str, runner: SubprocessRunner) -> CloneSnapshot | None:
    """Capture the clone's HEAD SHA before a worktree-skill session.

    Returns None on failure (graceful degradation — guard simply won't activate).
    """
    result = await runner(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        logger.debug("snapshot_clone_state_failed", returncode=result.returncode)
        return None
    head_sha = result.stdout.strip()
    if not head_sha:
        logger.debug("snapshot_clone_state_empty_sha")
        return None
    logger.debug("snapshot_clone_state_captured", head_sha=head_sha)
    return CloneSnapshot(head_sha=head_sha)


def _status_path_under_prefix(status_line: str, prefix: str) -> bool:
    """Return True if the file path in a git status --porcelain line is under prefix."""
    path_part = status_line[3:]  # Skip "XY " status chars
    if " -> " in path_part:
        path_part = path_part.split(" -> ")[-1]
    return path_part.startswith(prefix)


async def detect_contamination(
    snapshot: CloneSnapshot,
    cwd: str,
    runner: SubprocessRunner,
    *,
    exclude_prefix: str = "",
) -> ContaminationReport | None:
    """Check whether the clone directory was contaminated during the session.

    Returns None if no contamination detected, otherwise a ContaminationReport.
    """
    head_result = await runner(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    if head_result.returncode != 0:
        logger.debug("detect_contamination_rev_parse_failed", returncode=head_result.returncode)
        return None
    post_sha = head_result.stdout.strip()

    status_result = await runner(
        ["git", "status", "--porcelain"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    if status_result.returncode != 0:
        logger.debug("detect_contamination_status_failed", returncode=status_result.returncode)
        return None
    status_lines = [line for line in status_result.stdout.splitlines() if line.strip()]

    if exclude_prefix:
        status_lines = [
            line for line in status_lines if not _status_path_under_prefix(line, exclude_prefix)
        ]

    direct_commits = bool(post_sha and post_sha != snapshot.head_sha)
    uncommitted = len(status_lines) > 0

    if not direct_commits and not uncommitted:
        logger.debug("detect_contamination_clean")
        return None

    logger.warning(
        "clone_contamination_detected",
        pre_sha=snapshot.head_sha,
        post_sha=post_sha,
        uncommitted_file_count=len(status_lines),
        direct_commits=direct_commits,
    )
    return ContaminationReport(
        pre_sha=snapshot.head_sha,
        post_sha=post_sha,
        uncommitted_files=status_lines,
        direct_commits=direct_commits,
        reverted=False,
    )


async def revert_contamination(
    snapshot: CloneSnapshot,
    report: ContaminationReport,
    cwd: str,
    runner: SubprocessRunner,
    *,
    selective: bool = False,
    exclude_prefix: str = ".autoskillit/",
) -> ContaminationReport:
    """Revert the clone to its pre-session state.

    When *selective* is True (read-only or write-scoped skills), uses
    ``git checkout -- .`` and ``git clean -fd --exclude=<exclude_prefix>``
    to preserve legitimate output under *exclude_prefix*.
    Falls back to ``git reset --hard`` only when direct commits are present.
    """
    logger.info(
        "reverting_clone_contamination",
        pre_sha=snapshot.head_sha,
        direct_commits=report.direct_commits,
        uncommitted_file_count=len(report.uncommitted_files),
        selective=selective,
    )
    if selective:
        if report.direct_commits:
            reset_result = await runner(
                ["git", "reset", "--hard", snapshot.head_sha],
                cwd=Path(cwd),
                timeout=_GIT_TIMEOUT,
            )
            if reset_result.returncode != 0:
                return dataclasses.replace(report, reverted=False)
        checkout_result = await runner(
            ["git", "checkout", "--", "."],
            cwd=Path(cwd),
            timeout=_GIT_TIMEOUT,
        )
        if checkout_result.returncode != 0:
            return dataclasses.replace(report, reverted=False)
        clean_result = await runner(
            ["git", "clean", "-fd", f"--exclude={exclude_prefix}"],
            cwd=Path(cwd),
            timeout=_GIT_TIMEOUT,
        )
        return dataclasses.replace(report, reverted=clean_result.returncode == 0)

    reset_result = await runner(
        ["git", "reset", "--hard", snapshot.head_sha],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    if reset_result.returncode != 0:
        logger.warning(
            "revert_contamination_reset_failed",
            reset_rc=reset_result.returncode,
        )
        return dataclasses.replace(report, reverted=False)
    clean_result = await runner(
        ["git", "clean", "-fd"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    reverted = clean_result.returncode == 0
    if not reverted:
        logger.warning(
            "revert_contamination_failed",
            reset_rc=reset_result.returncode,
            clean_rc=clean_result.returncode,
        )
    return dataclasses.replace(report, reverted=reverted)


async def check_and_revert_clone_contamination(
    snapshot: CloneSnapshot | None,
    skill_result: SkillResult,
    cwd: str,
    runner: SubprocessRunner,
    audit: AuditLog | None,
    skill_command: str = "",
    *,
    policy: CloneGuardPolicy,
    exclude_prefix: str = ".autoskillit/",
) -> tuple[SkillResult, bool]:
    """Top-level guard: detect and revert clone contamination.

    Uses *policy* to decide whether to fire based on skill success/failure
    and how to revert (selective vs full reset).

    *exclude_prefix* is passed to ``revert_contamination`` when doing a selective
    revert — files under this prefix are preserved (not reverted).

    Returns (skill_result, reverted) where reverted is True if contamination
    was found and cleaned up.
    """
    if snapshot is None:
        return skill_result, False
    if not policy.should_fire(skill_result.success):
        return skill_result, False
    if skill_result.worktree_path is not None:
        return skill_result, False

    report = await detect_contamination(snapshot, cwd, runner, exclude_prefix=exclude_prefix)
    if report is None:
        return skill_result, False

    report = await revert_contamination(
        snapshot,
        report,
        cwd,
        runner,
        selective=policy.selective_revert,
        exclude_prefix=exclude_prefix,
    )

    if report.reverted:
        skill_result = dataclasses.replace(
            skill_result,
            success=False,
            subtype="clone_contamination",
            needs_retry=True,
            retry_reason=RetryReason.CLONE_CONTAMINATION,
        )

    if audit is not None:
        audit.record_failure(
            FailureRecord(
                timestamp=datetime.now(UTC).isoformat(),
                skill_command=skill_command,
                exit_code=skill_result.exit_code,
                subtype="clone_contamination",
                needs_retry=skill_result.needs_retry,
                retry_reason=RetryReason.CLONE_CONTAMINATION.value,
                stderr=(
                    f"pre_sha={report.pre_sha} post_sha={report.post_sha} "
                    f"files={len(report.uncommitted_files)} "
                    f"direct_commits={report.direct_commits}"
                ),
            )
        )

    logger.warning(
        "clone_contamination_reverted",
        pre_sha=report.pre_sha,
        post_sha=report.post_sha,
        files=len(report.uncommitted_files),
        direct_commits=report.direct_commits,
    )
    return skill_result, True

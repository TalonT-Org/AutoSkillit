"""Clone contamination guard — detect and revert direct changes to clone CWD.

L1 module (execution/). Detects when a worktree-based skill session modified
the clone directory directly (without creating a worktree) and reverts those
changes to prevent contamination from propagating to retry sessions.

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
    from autoskillit.core import AuditStore, SubprocessRunner

logger = get_logger(__name__)

WORKTREE_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree-no-merge",
        "retry-worktree",
    }
)

_GIT_TIMEOUT: float = 10.0


@dataclass(frozen=True)
class CloneSnapshot:
    """Pre-session state of the clone directory."""

    head_sha: str


@dataclass(frozen=True)
class ContaminationReport:
    """Details of detected clone contamination."""

    pre_sha: str
    post_sha: str
    uncommitted_files: list[str]
    direct_commits: bool
    reverted: bool


def is_worktree_skill(skill_command: str) -> bool:
    """Return True if skill_command invokes a worktree-creating skill."""
    return any(name in skill_command for name in WORKTREE_SKILLS)


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


async def detect_contamination(
    snapshot: CloneSnapshot, cwd: str, runner: SubprocessRunner
) -> ContaminationReport | None:
    """Check whether the clone directory was contaminated during the session.

    Returns None if no contamination detected, otherwise a ContaminationReport.
    """
    head_result = await runner(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    post_sha = head_result.stdout.strip() if head_result.returncode == 0 else ""

    status_result = await runner(
        ["git", "status", "--porcelain"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    status_lines = [line for line in status_result.stdout.splitlines() if line.strip()]

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
) -> ContaminationReport:
    """Revert the clone to its pre-session state."""
    logger.info(
        "reverting_clone_contamination",
        pre_sha=snapshot.head_sha,
        direct_commits=report.direct_commits,
        uncommitted_file_count=len(report.uncommitted_files),
    )
    await runner(
        ["git", "reset", "--hard", snapshot.head_sha],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    await runner(
        ["git", "clean", "-fd"],
        cwd=Path(cwd),
        timeout=_GIT_TIMEOUT,
    )
    return dataclasses.replace(report, reverted=True)


async def check_and_revert_clone_contamination(
    snapshot: CloneSnapshot | None,
    skill_result: SkillResult,
    cwd: str,
    runner: SubprocessRunner,
    audit: AuditStore | None,
    skill_command: str = "",
) -> tuple[SkillResult, bool]:
    """Top-level guard: detect and revert clone contamination after a failed session.

    Returns (skill_result, reverted) where reverted is True if contamination
    was found and cleaned up.
    """
    if snapshot is None:
        return skill_result, False
    if skill_result.success:
        return skill_result, False
    if skill_result.worktree_path is not None:
        return skill_result, False

    report = await detect_contamination(snapshot, cwd, runner)
    if report is None:
        return skill_result, False

    report = await revert_contamination(snapshot, report, cwd, runner)

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

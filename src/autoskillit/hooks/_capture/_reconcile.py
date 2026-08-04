"""Shared cleanup-owner adapter for capture lifecycle reconciliation."""

from __future__ import annotations

from collections.abc import Callable

from . import _authority, _migration
from ._failure_policy import CaptureFailureReason, runtime_failure_reason
from ._module_identity import register_module_aliases
from ._types import (
    CaptureCleanupOutcome,
    CleanupBlocker,
    CleanupProgress,
    SweepBudgetSpec,
)

register_module_aliases(__name__)

RUNNER_TAIL_BUDGET = SweepBudgetSpec()
SESSION_START_BUDGET = SweepBudgetSpec(
    max_attempts=256,
    max_transitions=1024,
    max_cursor_writes=256,
    max_duration_seconds=1.0,
)

__all__ = [
    "RUNNER_TAIL_BUDGET",
    "SESSION_START_BUDGET",
    "cleanup_diagnostic",
    "emit_bounded_diagnostic",
    "emit_runner_diagnostic",
    "reconcile_capture_store",
]


def _failure_outcome(blocker: CleanupBlocker) -> CaptureCleanupOutcome:
    return CaptureCleanupOutcome(errors=1, remaining_due=1, blocker=blocker)


_FAILURE_BLOCKERS = {
    CaptureFailureReason.PERMISSION_DENIED: CleanupBlocker.PERMISSION_DENIED,
    CaptureFailureReason.FILESYSTEM_IO: CleanupBlocker.FILESYSTEM_IO,
    CaptureFailureReason.LEDGER_INTEGRITY: CleanupBlocker.LEDGER_INTEGRITY,
    CaptureFailureReason.MIGRATION_BLOCKED: CleanupBlocker.MIGRATION_BLOCKED,
}


def _failure_blocker(reason: CaptureFailureReason) -> CleanupBlocker:
    return _FAILURE_BLOCKERS.get(reason, CleanupBlocker.FILESYSTEM_AUTHORITY)


def reconcile_capture_store(
    project_cwd: str,
    budget: SweepBudgetSpec,
) -> CaptureCleanupOutcome:
    if not isinstance(project_cwd, str) or not project_cwd or type(budget) is not SweepBudgetSpec:
        return _failure_outcome(CleanupBlocker.FILESYSTEM_AUTHORITY)
    try:
        with _authority.open_capture_lifecycle(project_cwd, create=False) as lifecycle:
            return lifecycle.sweep(budget)
    except _authority.CaptureStoreAbsentError:
        return CaptureCleanupOutcome(blocker=CleanupBlocker.STORE_ABSENT)
    except _migration.MigrationBlockedError:
        return CaptureCleanupOutcome(
            remaining_due=1,
            progress=CleanupProgress.CURSOR_ADVANCED,
            blocker=CleanupBlocker.MIGRATION_BLOCKED,
        )
    except (
        _authority.CaptureSetupError,
        _migration.MigrationAuthorityError,
        _migration.MigrationIntegrityError,
        _authority.CaptureLifecycleError,
        OSError,
    ) as exc:
        return _failure_outcome(_failure_blocker(runtime_failure_reason(exc)))


def cleanup_diagnostic(
    outcome: CaptureCleanupOutcome,
    *,
    owner: str,
    normalize: Callable[[str], str] | None = None,
) -> str | None:
    if type(outcome) is not CaptureCleanupOutcome:
        detail = "capture cleanup returned an invalid outcome"
    elif (
        outcome.errors == 0
        and outcome.blocker in {CleanupBlocker.NONE, CleanupBlocker.STORE_ABSENT}
        and (
            outcome.progress is not CleanupProgress.NONE
            or outcome.remaining_due == 0
            or outcome.blocker is CleanupBlocker.STORE_ABSENT
        )
    ):
        return None
    else:
        detail = (
            f"capture cleanup {owner} deferred: progress={outcome.progress.value} "
            f"blocker={outcome.blocker.value} errors={outcome.errors} "
            f"remaining_due={outcome.remaining_due}"
        )
    return detail if normalize is None else normalize(detail)


def emit_bounded_diagnostic(
    detail: str,
    *,
    maximum_bytes: int,
    write: Callable[[str], object],
) -> None:
    safe = " ".join(detail.split()).replace("]", "\\u005d")
    bounded = safe.encode("utf-8")[:maximum_bytes].decode("utf-8", errors="ignore")
    try:
        write(bounded)
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        pass


def emit_runner_diagnostic(detail: str, write: Callable[[str], object]) -> None:
    emit_bounded_diagnostic(
        f"[AutoSkillit shell capture cleanup failed: {detail}]\n",
        maximum_bytes=240,
        write=write,
    )

"""Shared cleanup-owner adapter for capture lifecycle reconciliation."""

from __future__ import annotations

import errno
from collections.abc import Callable

from . import _authority, _migration
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


def _os_blocker(exc: OSError) -> CleanupBlocker:
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return CleanupBlocker.PERMISSION_DENIED
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        return CleanupBlocker.FILESYSTEM_AUTHORITY
    return CleanupBlocker.FILESYSTEM_IO


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
    except _authority.CaptureSetupError as exc:
        reason = exc.reason
        if reason.value == "PERMISSION_DENIED":
            return _failure_outcome(CleanupBlocker.PERMISSION_DENIED)
        if reason.value == "FILESYSTEM_IO":
            return _failure_outcome(CleanupBlocker.FILESYSTEM_IO)
        return _failure_outcome(CleanupBlocker.FILESYSTEM_AUTHORITY)
    except _migration.MigrationBlockedError:
        return CaptureCleanupOutcome(
            remaining_due=1,
            progress=CleanupProgress.CURSOR_ADVANCED,
            blocker=CleanupBlocker.MIGRATION_BLOCKED,
        )
    except _migration.MigrationAuthorityError:
        return _failure_outcome(CleanupBlocker.FILESYSTEM_AUTHORITY)
    except _migration.MigrationIntegrityError:
        return _failure_outcome(CleanupBlocker.LEDGER_INTEGRITY)
    except _authority.CaptureLifecycleError:
        return _failure_outcome(CleanupBlocker.LEDGER_INTEGRITY)
    except OSError as exc:
        return _failure_outcome(_os_blocker(exc))


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

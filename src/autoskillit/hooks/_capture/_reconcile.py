"""Shared cleanup-owner adapter for capture lifecycle reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import _authority, _migration
from ._failure_policy import CaptureFailureReason, runtime_failure_reason
from ._module_identity import register_module_aliases
from ._types import (
    CaptureCleanupOutcome,
    CleanupBlocker,
    CleanupProgress,
    CleanupSeverity,
    SweepBudgetSpec,
    classify_cleanup_outcome,
)

if TYPE_CHECKING:
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix
elif __package__ == "_capture":
    from _policy_event import PolicyEvent, render_provenance_prefix
else:
    from .._policy_event import PolicyEvent, render_provenance_prefix

register_module_aliases(__name__)

RUNNER_TAIL_BUDGET = SweepBudgetSpec()
SESSION_START_BUDGET = SweepBudgetSpec(
    max_attempts=256,
    max_transitions=1024,
    max_cursor_writes=256,
    max_duration_seconds=1.0,
)

# Single shared byte bound for every capture-cleanup diagnostic, regardless of
# owner — replaces the runner-tail (240) vs. SessionStart (512) divergence.
DIAGNOSTIC_MAX_BYTES = 400
_HOOK_VERSION = 1
_POLICY_EVENT_NAME = "capture_cleanup"

__all__ = [
    "DIAGNOSTIC_MAX_BYTES",
    "RUNNER_TAIL_BUDGET",
    "SESSION_START_BUDGET",
    "cleanup_diagnostic",
    "emit_bounded_diagnostic",
    "emit_owner_diagnostic",
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


def _outcome_severity(outcome: CaptureCleanupOutcome) -> CleanupSeverity | None:
    if type(outcome) is not CaptureCleanupOutcome:
        return None
    return classify_cleanup_outcome(outcome.progress, outcome.blocker, outcome.errors)


def cleanup_diagnostic(
    outcome: CaptureCleanupOutcome,
    *,
    owner: str,
    normalize: Callable[[str], str] | None = None,
) -> str | None:
    severity = _outcome_severity(outcome)
    if severity is None:
        detail = "capture cleanup returned an invalid outcome"
        return detail if normalize is None else normalize(detail)
    if severity in (CleanupSeverity.HEALTHY, CleanupSeverity.DEFERRED):
        return None
    if severity is CleanupSeverity.STALLED:
        detail = (
            f"capture cleanup {owner} deferred without progress: "
            f"blocker={outcome.blocker.value} remaining_due={outcome.remaining_due}"
        )
    else:
        detail = (
            f"capture cleanup {owner} outcome: progress={outcome.progress.value} "
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


def emit_owner_diagnostic(
    outcome: CaptureCleanupOutcome,
    *,
    owner: str,
    write: Callable[[str], object],
) -> None:
    """Render and emit a cleanup-outcome diagnostic for the given owner, if any.

    Owner-neutral: the SessionStart cleanup hook and the per-command
    runner-tail sweep both call this with their own ``owner`` label. Silent
    for HEALTHY/DEFERRED severity; STALLED/FAILED render through the shared
    PolicyEvent formatter — FAILED is the only severity whose rendered text
    may contain "failed".
    """
    detail = cleanup_diagnostic(outcome, owner=owner)
    if detail is None:
        return
    severity = _outcome_severity(outcome) or CleanupSeverity.FAILED
    event = PolicyEvent(
        hook_id=owner,
        hook_version=_HOOK_VERSION,
        event=_POLICY_EVENT_NAME,
        decision=severity.value,
        reason_code=detail,
    )
    emit_bounded_diagnostic(
        render_provenance_prefix(event),
        maximum_bytes=DIAGNOSTIC_MAX_BYTES,
        write=write,
    )

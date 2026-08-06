"""Shared cleanup-owner adapter for capture lifecycle reconciliation."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import _authority, _migration, _orphan_scan
from ._failure_policy import CaptureFailureReason, runtime_failure_reason
from ._lifecycle_policy import CaptureRetentionPhase
from ._module_identity import register_module_aliases
from ._syntax import PUBLIC_NAME_RE
from ._types import (
    CaptureCleanupOutcome,
    CleanupBlocker,
    CleanupProgress,
    CleanupSeverity,
    LockContended,
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
    # Only SESSION_START scans for directory-reconciliation orphans — the
    # runner-tail budget above stays at the SweepBudgetSpec default (0,
    # disabled) so per-command latency is unaffected.
    max_directory_entries_scanned=512,
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
    "CaptureStoreStats",
    "capture_store_stats",
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
    started = time.monotonic()
    try:
        with _authority.open_capture_lifecycle(
            project_cwd, create=False, open_budget=budget
        ) as lifecycle:
            return lifecycle.sweep(budget)
    except _authority.CaptureStoreAbsentError:
        return CaptureCleanupOutcome(blocker=CleanupBlocker.STORE_ABSENT)
    except LockContended:
        # The store-open lock acquisition (interrupted-delivery
        # normalization) exhausted the entire budget without ever
        # acquiring — the same LOCK_CONTENDED outcome run_bounded_sweep
        # reports for sweep-body contention, surfaced one layer up since
        # the sweep body was never reached at all.
        return CaptureCleanupOutcome(
            remaining_due=1,
            blocker=CleanupBlocker.LOCK_CONTENDED,
            duration=max(0.0, time.monotonic() - started),
        )
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


@dataclass(frozen=True, slots=True)
class CaptureStoreStats:
    """Read-only ledger and directory statistics for the capture store.

    ``blocker`` reuses the sweep-owner vocabulary: ``NONE`` means every
    other field is populated, ``STORE_ABSENT`` means no store exists yet
    (a healthy nothing-to-report case, matching ``reconcile_capture_store``),
    any other value means stats could not be gathered.
    """

    blocker: CleanupBlocker
    live_records: int = 0
    eligible_records: int = 0
    deleting_records: int = 0
    ledger_bytes: int = 0
    directory_files: int = 0
    unledgered_aged_files: int = 0
    unledgered_aged_bytes: int = 0


def capture_store_stats(project_cwd: str) -> CaptureStoreStats:
    """Report capture-store ledger and directory statistics without mutating.

    No cursor writes, no admission checks, no deletions, no ledger appends —
    the doctor battery's read-only check and ``autoskillit capture-store``
    (without ``--reclaim``) both call this and only this, so neither surface
    can drift from what a real reconciliation pass would find. A diagnostic
    read must never hang: opened with RUNNER_TAIL_BUDGET as its open_budget
    so a contended lock — at store-open time or this function's own record
    load — is bounded the same way a real sweep would be, never blocking
    indefinitely.
    """
    if not isinstance(project_cwd, str) or not project_cwd:
        return CaptureStoreStats(CleanupBlocker.FILESYSTEM_AUTHORITY)
    try:
        with _authority.open_capture_lifecycle(
            project_cwd, create=False, open_budget=RUNNER_TAIL_BUDGET
        ) as store:
            with store._locked(blocking=False):
                records, _compaction_epoch, ledger_bytes = store._load_locked()
            tracked = frozenset(
                record.public_name
                for record in records.values()
                if record.retention_phase is not CaptureRetentionPhase.DELETED
            )
            now = store._wall_clock()
            directory_files = 0
            unledgered_aged_files = 0
            unledgered_aged_bytes = 0
            for entry in os.scandir(store._root_fd):
                directory_files += 1
                if PUBLIC_NAME_RE.fullmatch(entry.name) is None or entry.name in tracked:
                    continue
                try:
                    value = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(value.st_mode):
                    continue
                if now - value.st_mtime >= _orphan_scan.ADOPTION_AGE_SECONDS:
                    unledgered_aged_files += 1
                    unledgered_aged_bytes += value.st_size
            return CaptureStoreStats(
                blocker=CleanupBlocker.NONE,
                live_records=sum(
                    1
                    for record in records.values()
                    if record.retention_phase is not CaptureRetentionPhase.DELETED
                ),
                eligible_records=sum(
                    1
                    for record in records.values()
                    if record.retention_phase is CaptureRetentionPhase.ELIGIBLE
                ),
                deleting_records=sum(
                    1
                    for record in records.values()
                    if record.retention_phase is CaptureRetentionPhase.DELETING
                ),
                ledger_bytes=ledger_bytes,
                directory_files=directory_files,
                unledgered_aged_files=unledgered_aged_files,
                unledgered_aged_bytes=unledgered_aged_bytes,
            )
    except _authority.CaptureStoreAbsentError:
        return CaptureStoreStats(CleanupBlocker.STORE_ABSENT)
    except LockContended:
        return CaptureStoreStats(CleanupBlocker.LOCK_CONTENDED)
    except _migration.MigrationBlockedError:
        return CaptureStoreStats(CleanupBlocker.MIGRATION_BLOCKED)
    except (
        _authority.CaptureSetupError,
        _migration.MigrationAuthorityError,
        _migration.MigrationIntegrityError,
        _authority.CaptureLifecycleError,
        OSError,
    ) as exc:
        return CaptureStoreStats(_failure_blocker(runtime_failure_reason(exc)))


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
            f"capture cleanup {owner} stalled without progress: "
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

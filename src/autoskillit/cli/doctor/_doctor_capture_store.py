"""Capture-store ledger/directory statistics doctor check."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity
from autoskillit.hooks import CaptureStoreStats, CleanupBlocker, capture_store_stats

from ._doctor_types import DoctorResult

# Above this many aged unledgered files, surface a WARNING nudging toward
# `autoskillit capture-store --reclaim` rather than staying silently OK —
# a handful of aged orphans is unremarkable churn; hundreds is the debris
# field this whole reconciliation mechanism exists to prevent.
_UNLEDGERED_WARNING_THRESHOLD = 100
# Production warns at the default assist tier, before the higher stall tier
# begins refusing new captures. Runtime capacity overrides remain runner-local.
_RECLAMATION_DEBT_WARNING_THRESHOLD = 256


def _capacity_line(stats: CaptureStoreStats) -> tuple[bool, str]:
    utilization_pct = (
        f"{stats.projected_compacted_bytes / stats.compaction_low_bytes:.1%}"
        if stats.compaction_low_bytes > 0
        else "n/a"
    )
    return stats.capacity_warning, (
        f"capacity: projected_compacted_bytes={stats.projected_compacted_bytes} "
        f"low_bytes={stats.compaction_low_bytes} utilization_pct={utilization_pct} "
        "(default capacity limits; runtime overrides are runner-local)"
    )


def _check_capture_store_stats(project_dir: Path | None = None) -> DoctorResult:
    """Report capture-store ledger/directory statistics — read-only, never reclaims.

    Mutation (adopting orphans, deleting eligible records) is deliberately
    out of scope: ``run_doctor()`` is architecturally read-only
    (``tests/arch/test_doctor_readonly.py``); ``autoskillit capture-store
    --reclaim`` is the mutating counterpart this check points readers to.
    """
    root = project_dir or Path.cwd()
    stats = capture_store_stats(str(root))
    if stats.blocker is CleanupBlocker.STORE_ABSENT:
        return DoctorResult(Severity.OK, "capture_store_stats", "No capture store yet")
    if stats.blocker is not CleanupBlocker.NONE:
        return DoctorResult(
            Severity.WARNING,
            "capture_store_stats",
            f"Capture-store stats unavailable (blocker={stats.blocker.value})",
        )
    message = (
        f"ledger: live={stats.live_records} due_records={stats.due_records} "
        f"eligible_abandoned={stats.eligible_records} "
        f"deleting={stats.deleting_records} ledger_bytes={stats.ledger_bytes}; "
        f"directory: files={stats.directory_files} "
        f"unledgered_aged={stats.unledgered_aged_files} "
        f"unledgered_aged_bytes={stats.unledgered_aged_bytes}"
    )
    capacity_warning, capacity_message = _capacity_line(stats)
    message = f"{message}; {capacity_message}"
    if (
        capacity_warning
        or stats.unledgered_aged_files >= _UNLEDGERED_WARNING_THRESHOLD
        or stats.due_records >= _RECLAMATION_DEBT_WARNING_THRESHOLD
    ):
        return DoctorResult(
            Severity.WARNING,
            "capture_store_stats",
            f"{message}. Run 'autoskillit capture-store --reclaim' to reclaim the backlog.",
        )
    return DoctorResult(Severity.OK, "capture_store_stats", message)

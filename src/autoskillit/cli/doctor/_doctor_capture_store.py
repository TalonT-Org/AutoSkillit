"""Capture-store ledger/directory statistics doctor check."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity, get_logger
from autoskillit.hooks import CleanupBlocker, capture_store_stats

from ._doctor_types import DoctorResult

logger = get_logger(__name__)

# Above this many aged unledgered files, surface a WARNING nudging toward
# `autoskillit capture-store --reclaim` rather than staying silently OK —
# a handful of aged orphans is unremarkable churn; hundreds is the debris
# field this whole reconciliation mechanism exists to prevent.
_UNLEDGERED_WARNING_THRESHOLD = 100


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
        f"ledger: live={stats.live_records} eligible={stats.eligible_records} "
        f"deleting={stats.deleting_records} ledger_bytes={stats.ledger_bytes}; "
        f"directory: files={stats.directory_files} "
        f"unledgered_aged={stats.unledgered_aged_files} "
        f"unledgered_aged_bytes={stats.unledgered_aged_bytes}"
    )
    if stats.unledgered_aged_files >= _UNLEDGERED_WARNING_THRESHOLD:
        return DoctorResult(
            Severity.WARNING,
            "capture_store_stats",
            f"{message}. Run 'autoskillit capture-store --reclaim' to reclaim the backlog.",
        )
    return DoctorResult(Severity.OK, "capture_store_stats", message)

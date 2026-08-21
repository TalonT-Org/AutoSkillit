"""Opt-in doctor repair actions, kept separate from read-only diagnostics."""

from __future__ import annotations

from autoskillit.core import Severity, repair_corrupt_retiring_cache

from ._doctor_types import DoctorResult


def collect_retiring_cache_repair_results() -> list[DoctorResult]:
    """Repair the retiring cache when safe and describe the action."""
    repair = repair_corrupt_retiring_cache()
    if repair.repaired:
        message = (
            f"Retiring cache repaired; original bytes preserved at {repair.sidecar}; "
            f"salvaged={repair.salvaged}, quarantined={repair.quarantined}."
        )
    elif repair.state.value == "unsupported_future":
        message = (
            "Retiring cache was not repaired because its schema is newer than this "
            "AutoSkillit version; upgrade before rewriting it."
        )
    else:
        message = f"Retiring cache repair was not needed (state={repair.state.value})."
    return [
        DoctorResult(
            severity=Severity.INFO,
            check="retiring_cache_repair",
            message=message,
        )
    ]

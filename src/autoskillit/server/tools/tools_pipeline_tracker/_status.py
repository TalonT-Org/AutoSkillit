"""Status computation helpers for the pipeline tracker tool."""

from __future__ import annotations


def _compute_status_counts(
    steps: dict[str, dict[str, object]], dependencies: dict[str, list[str]]
) -> dict[str, int]:
    return {
        "complete": sum(1 for s in steps.values() if s.get("status") == "complete"),
        "pending": sum(1 for s in steps.values() if s.get("status") == "pending"),
        "skipped": sum(1 for s in steps.values() if s.get("status") == "skipped"),
        "blocked": sum(
            1
            for sname, sdata in steps.items()
            if sdata.get("status") == "pending" and sname in dependencies
        ),
    }

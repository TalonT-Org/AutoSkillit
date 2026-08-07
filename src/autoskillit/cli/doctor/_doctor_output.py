"""Output formatting for doctor results."""

from __future__ import annotations

import json

from ._doctor_types import _NON_PROBLEM, DoctorResult


def _format_results(results: list[DoctorResult], *, output_json: bool) -> list[str]:
    """Format doctor results without owning the CLI output stream."""
    if output_json:
        return [
            json.dumps(
                {
                    "results": [
                        {"severity": r.severity, "check": r.check, "message": r.message}
                        for r in results
                    ]
                },
                indent=2,
            )
        ]

    has_problems = any(r.severity not in _NON_PROBLEM for r in results)
    if has_problems:
        return [
            f"{r.severity.upper()}: {r.message}" for r in results if r.severity not in _NON_PROBLEM
        ]
    return [f"{r.severity}: {r.message}" for r in results]

"""Shared DoctorResult type — imported by all _doctor_* sub-modules."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from autoskillit.core import Severity


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


_NON_PROBLEM: frozenset[Severity] = frozenset({Severity.OK, Severity.INFO})


def _format_results(
    results: list[DoctorResult],
    *,
    output_json: bool,
    include_info: bool = False,
) -> list[str]:
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
            f"{r.severity.upper()}: {r.message}"
            for r in results
            if r.severity not in _NON_PROBLEM or (include_info and r.severity is Severity.INFO)
        ]
    return [f"{r.severity}: {r.message}" for r in results]


def _print_doctor_results(
    results: list[DoctorResult],
    *,
    output_json: bool,
    include_info: bool = False,
) -> None:
    """Send one formatted doctor result set to the CLI output stream."""
    for line in _format_results(
        results,
        output_json=output_json,
        include_info=include_info,
    ):
        sys.stdout.write(line + "\n")

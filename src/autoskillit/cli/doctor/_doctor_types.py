"""Shared DoctorResult type — imported by all _doctor_* sub-modules."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from autoskillit.core import Severity, get_logger

logger = get_logger(__name__)


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


def _check_display_name(fn: Callable[[], object]) -> str:
    """Best-effort check name for logging/reporting — must never raise, since
    it is also used on the exception path inside _run_check."""
    name = getattr(getattr(fn, "func", fn), "__name__", "unknown")
    return name.removeprefix("_check_") if isinstance(name, str) else "unknown"


def _run_check(
    fn: Callable[[], object],
    *,
    check_name: str | None = None,
) -> list[DoctorResult]:
    """Invoke one doctor check, isolating any exception as a single ERROR result.

    Crash-path check names come from the callable name unless supplied explicitly.
    """
    resolved_check_name = check_name or _check_display_name(fn)
    try:
        result = fn()
        if isinstance(result, DoctorResult):
            return [result]
        if isinstance(result, list) and all(isinstance(item, DoctorResult) for item in result):
            return result
        raise TypeError("doctor check must return DoctorResult or list[DoctorResult]")
    except Exception as exc:  # noqa: BLE001 - isolates one check from all others
        logger.exception("doctor_check_crashed", check=resolved_check_name)
        return [DoctorResult(Severity.ERROR, resolved_check_name, f"Check crashed: {exc}")]


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

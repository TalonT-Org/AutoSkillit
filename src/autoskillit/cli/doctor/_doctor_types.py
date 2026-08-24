"""Shared DoctorResult type — imported by all _doctor_* sub-modules."""

from __future__ import annotations

import functools
import json
import sys
from dataclasses import dataclass
from typing import Any

from autoskillit.core import Severity, get_logger

logger = get_logger(__name__)


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


def _check_display_name(fn: functools.partial[Any]) -> str:
    """Best-effort check name for logging/reporting — must never raise, since
    it is also used on the exception path inside _run_check."""
    name = getattr(getattr(fn, "func", fn), "__name__", "unknown")
    return name.removeprefix("_check_")


def _run_check(
    fn: functools.partial[DoctorResult] | functools.partial[list[DoctorResult]],
) -> list[DoctorResult]:
    """Invoke one doctor check, isolating any exception as a single ERROR result.

    Every _check_* invocation in _collect_doctor_results routes through this —
    enforced by tests/arch/test_ast_rules.py::test_no_bare_check_invocation_outside_run_check
    — so a bug in any one check (this class of TOCTOU race, or any other) cannot
    crash the other 54 unrelated checks. check_name is derived before the try
    via a getattr-with-default helper that cannot itself raise — the isolation
    guarantee must not have an escape hatch on its own bookkeeping.

    Caution: check_name (and thus the crash-path DoctorResult.check value) is
    derived from the check *function's* name, minus the `_check_` prefix — not
    from the `.check` string that function emits on its own success path. Most
    checks keep these identical by convention, but a handful of fan-out/nested
    checks do not (see the known limitation noted where _run_check is wired in
    at each call site). Do not build `.check`-keyed logic against a crash-path
    value without confirming the specific check keeps that convention.
    """
    check_name = _check_display_name(fn)
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - isolates one check from all others
        logger.exception("doctor_check_crashed", check=check_name)
        return [DoctorResult(Severity.ERROR, check_name, f"Check crashed: {exc}")]
    return result if isinstance(result, list) else [result]


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

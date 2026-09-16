"""Outcome vocabulary and aggregation for fixed-set join batches."""

from __future__ import annotations

from .declaration import OUTCOME_PENDING, WAVE_PENDING

OUTCOME_SUCCESS = "success"

OUTCOME_FAILURE = "failure"

OUTCOME_LAUNCH_FAILED = "launch-failed"

OUTCOME_TIMEOUT = "timeout"

OUTCOME_CANCELLED = "cancelled"

OUTCOME_INTERRUPTION = "interruption"

OUTCOME_MISSING = "missing"

OUTCOME_REAPED = "reaped"

WAVE_COMPLETE = "complete"

WAVE_PARTIAL_TIMEOUT = "partial_timeout"

WAVE_FAILURE = "failure"

WAVE_LAUNCH_FAILED = "launch_failed"

WAVE_CANCELLED = "cancelled"

WAVE_INTERRUPTION = "interruption"

WAVE_MISSING_CHILD = "missing_child"

WAVE_REAPED = "reaped"

WAVE_PARTIAL = "partial"

_NON_SUCCESS_WAVE_OUTCOMES: frozenset[str] = frozenset(
    {
        WAVE_PARTIAL_TIMEOUT,
        WAVE_FAILURE,
        WAVE_LAUNCH_FAILED,
        WAVE_CANCELLED,
        WAVE_INTERRUPTION,
        WAVE_MISSING_CHILD,
        WAVE_REAPED,
        WAVE_PARTIAL,
    }
)

_TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_FAILURE,
        OUTCOME_LAUNCH_FAILED,
        OUTCOME_TIMEOUT,
        OUTCOME_CANCELLED,
        OUTCOME_INTERRUPTION,
        OUTCOME_MISSING,
        OUTCOME_REAPED,
    }
)

_COMPLETED_OUTCOMES: frozenset[str] = frozenset({OUTCOME_SUCCESS})


def is_terminal_outcome(outcome: object) -> bool:
    """Return whether ``outcome`` is a terminal assignment outcome."""
    return outcome in _TERMINAL_OUTCOMES


def _aggregate_wave_outcome(assignments: list[object]) -> str:
    if not assignments:
        return WAVE_MISSING_CHILD
    entries = [entry for entry in assignments if isinstance(entry, dict)]
    outcomes = [str(entry.get("outcome", OUTCOME_PENDING)) for entry in entries]
    if len(entries) != len(assignments) or any(outcome == OUTCOME_PENDING for outcome in outcomes):
        return WAVE_PENDING
    distinct_outcomes = frozenset(outcomes)
    if any(entry.get("cleanup_outcome") == OUTCOME_REAPED for entry in entries):
        return WAVE_REAPED
    if distinct_outcomes <= _COMPLETED_OUTCOMES:
        return WAVE_COMPLETE
    if OUTCOME_LAUNCH_FAILED in distinct_outcomes:
        return WAVE_LAUNCH_FAILED
    if OUTCOME_INTERRUPTION in distinct_outcomes:
        return WAVE_INTERRUPTION
    if OUTCOME_CANCELLED in distinct_outcomes:
        return WAVE_CANCELLED
    if OUTCOME_TIMEOUT in distinct_outcomes:
        return WAVE_PARTIAL_TIMEOUT
    if OUTCOME_FAILURE in distinct_outcomes:
        return WAVE_FAILURE
    if distinct_outcomes == {OUTCOME_MISSING}:
        return WAVE_MISSING_CHILD
    return WAVE_PARTIAL

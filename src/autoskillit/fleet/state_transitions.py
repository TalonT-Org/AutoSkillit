"""Fleet dispatch state-machine authority.

Owns the ``DispatchStatus`` enum and the transition table that drives
``_validate_transition``. Decomposed from ``state_types`` (#4856);
the dependency arrow now points ``state_records → state_transitions``
only — this module imports nothing from ``state_records``.
"""

from __future__ import annotations

from enum import StrEnum

from autoskillit.core import RetryReason


class DispatchStatus(StrEnum):
    """Status of a single dispatch within a campaign."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"
    RESUMABLE = "resumable"
    SKIPPED = "skipped"
    REFUSED = "refused"
    RELEASED = "released"
    UNKNOWN = "unknown"

    @classmethod
    def from_persisted(cls, raw: str) -> DispatchStatus:
        """Convert a persisted status string to a DispatchStatus member.

        Unknown strings map to UNKNOWN instead of raising ValueError.
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    DispatchStatus.PENDING: frozenset(
        {
            DispatchStatus.RUNNING,
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.SKIPPED,
            DispatchStatus.REFUSED,
            DispatchStatus.RELEASED,
        }
    ),
    DispatchStatus.RUNNING: frozenset(
        {
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
            DispatchStatus.RESUMABLE,
        }
    ),
    DispatchStatus.RESUMABLE: frozenset(
        {
            DispatchStatus.RUNNING,
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
        }
    ),
    DispatchStatus.FAILURE: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.SUCCESS: frozenset(),
    DispatchStatus.INTERRUPTED: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.SKIPPED: frozenset(),
    DispatchStatus.REFUSED: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.RELEASED: frozenset(),
    DispatchStatus.UNKNOWN: frozenset({DispatchStatus.UNKNOWN}),
}

for _ds in DispatchStatus:
    if _ds not in _ALLOWED_TRANSITIONS:
        raise AssertionError(f"DispatchStatus.{_ds.name} missing from _ALLOWED_TRANSITIONS")
# _ds intentionally leaks as the last iterated status — matches the original
# state_types.py assertion-loop pattern.


def _validate_transition(current: str, new: str, dispatch_name: str) -> None:
    """Raise ValueError if the status transition is not allowed."""
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is not None and new not in allowed:
        msg = f"Invalid transition for dispatch '{dispatch_name}': {current!r} -> {new!r}"
        raise ValueError(msg)


_COMPLETED_STATUSES = frozenset(
    {DispatchStatus.SUCCESS, DispatchStatus.SKIPPED, DispatchStatus.FAILURE}
)

_VISIBLE_IN_BLOCK_STATUSES = _COMPLETED_STATUSES | frozenset(
    {
        DispatchStatus.RELEASED,
        DispatchStatus.RUNNING,
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
    }
)

TERMINAL_DISPATCH_STATUSES: frozenset[str] = frozenset(
    status for status, transitions in _ALLOWED_TRANSITIONS.items() if not transitions
)

TERMINAL_UNCLEANED_STATUSES: frozenset[DispatchStatus] = frozenset(
    {DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED}
)

_ABANDON_REASONS: frozenset[str] = frozenset(
    {
        RetryReason.STALE,
        RetryReason.THINKING_STALL,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CLONE_CONTAMINATION,
        RetryReason.IDLE_STALL,
        RetryReason.CANCELLED,  # transport teardown — session was never started or was torn down
    }
)


__all__ = [
    "DispatchStatus",
    "_ALLOWED_TRANSITIONS",
    "_validate_transition",
    "_COMPLETED_STATUSES",
    "_VISIBLE_IN_BLOCK_STATUSES",
    "TERMINAL_DISPATCH_STATUSES",
    "TERMINAL_UNCLEANED_STATUSES",
    "_ABANDON_REASONS",
]
